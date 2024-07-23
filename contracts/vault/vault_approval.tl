#pragma version 9
#tealish version git+https://github.com/Hipo/tealish.git@df4b0130e0c23a3498dda2b2f1a21b3249530813

# Tinyman Governance - Vault Application
# License: https://github.com/tinymanorg/tinyman-governance/blob/main/LICENSE
# Documentation: https://docs.tinyman.org

# Name: <address>
# Size: 32 bytes
struct AccountState:
    locked_amount: int
    lock_end_time: int
    power_count: int
    deleted_power_count: int
end

# Name: <address><box_index><array_index>
# Size: 48 bytes (8+8+16+16). 1 Box(1008) = 21 AccountPower
struct AccountPower:
    bias: int
    timestamp: int
    slope: bytes[16]
    cumulative_power: bytes[16]
end

# Name: tp<box_index><array_index>
# Size: 48 bytes (8+8+16+16). 1 Box(1008) = 21 TotalPower
struct TotalPower:
    bias: int
    timestamp: int
    slope: bytes[16]
    cumulative_power: bytes[16]
end

# sc<timestamp>
# 16 bytes
struct SlopeChange:
    slope_delta: bytes[16]
end

# 7 * DAY
const int WEEK = 604800
# 4 * 52 * WEEK
const int MAX_LOCK_TIME = 125798400
const int MIN_LOCK_AMOUNT = 10000000
const int MIN_LOCK_AMOUNT_INCREMENT = 10000000
const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"

# Boxes
const bytes TOTAL_POWER_BOX_PREFIX = "tp"
const int TOTAL_POWER_SIZE = 48
const int TOTAL_POWER_BOX_SIZE = 1008
const int TOTAL_POWER_BOX_ARRAY_LEN = 21

const int ACCOUNT_STATE_SIZE = 32

const int ACCOUNT_POWER_SIZE = 48
const int ACCOUNT_POWER_BOX_SIZE = 1008
const int ACCOUNT_POWER_BOX_ARRAY_LEN = 21

const bytes SLOPE_CHANGE_BOX_PREFIX = "sc"
const int SLOPE_CHANGE_SIZE = 16

# Keys
const bytes TINY_ASSET_ID_KEY = "tiny_asset_id"
const bytes TOTAL_LOCKED_AMOUNT_KEY = "total_locked_amount"
const bytes TOTAL_POWER_COUNT_KEY = "total_power_count"
const bytes LAST_TOTAL_POWER_TIMESTAMP = "last_total_power_timestamp"


router:
    create_application
    increase_budget
    create_lock
    increase_lock_amount
    extend_lock_end_time
    create_checkpoints
    get_tiny_power_of
    get_tiny_power_of_at
    get_total_tiny_power
    get_total_tiny_power_at
    get_cumulative_power_of_at
    get_total_cumulative_power_at
    get_account_cumulative_power_delta
    get_total_cumulative_power_delta
    withdraw
    get_box
    delete_account_power_boxes
    delete_account_state
    init
end


@public(OnCompletion=CreateApplication)
func create_application(tiny_asset_id: int):
    app_global_put(TINY_ASSET_ID_KEY, tiny_asset_id)
    app_global_put(TOTAL_LOCKED_AMOUNT_KEY, 0)
    app_global_put(TOTAL_POWER_COUNT_KEY, 0)
    app_global_put(LAST_TOTAL_POWER_TIMESTAMP, 0)
    return
end

@public()
func increase_budget():
    # Box read budget and opcode budget will be incread using this method.
    return
end

@public()
func init():
    # create the first total power
    assert(!app_global_get(TOTAL_POWER_COUNT_KEY))
    TotalPower total_power = construct_total_power(0, Global.LatestTimestamp, bzero(16), bzero(16))
    save_total_power(total_power)

    # opt-in to TINY
    tiny_transfer(0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
    log(method("init()"))
    return
end

@public()
func create_checkpoints():
    create_checkpoints_internal(0, bzero(16), 0)
    log(method("create_checkpoints()"))
    return
end

@public()
func create_lock(lock_end_time: int):
    # This method is responsible for initiating a new lock for two categories of accounts:
    # 1) Accounts which have never had a token lock before.
    # 2) Accounts that have previously withdrawn their locked token amount.

    bytes[32] user_address = Txn.Sender
    int amount = get_received_tiny_amount()

    # amount must be at least the minimum
    assert(amount >= MIN_LOCK_AMOUNT)
    # lock end time must be a the start of a week
    assert(!(lock_end_time % WEEK))
    # lock duration must be at least 4 weeks
    assert(lock_end_time >= (Global.LatestTimestamp + (4 * WEEK)))
    # lock duration must be no more than the max lock time
    assert(lock_end_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))

    # Get or create the account state box
    # An account may have state box if it is not locking first time.
    _ = box_create(user_address, ACCOUNT_STATE_SIZE)
    box<AccountState> account_state = Box(user_address)

    # Ensure that the account either has no previous lock or any existing locks have been fully withdrawn
    assert(!account_state.locked_amount)

    # Update the account state
    account_state.locked_amount = amount
    account_state.lock_end_time = lock_end_time

    # Increase the power count by one
    int account_power_index = account_state.power_count
    account_state.power_count = account_power_index + 1

    # Calculate the current slope and bias
    bytes slope = get_slope(amount)
    int bias = get_bias(slope, (lock_end_time - Global.LatestTimestamp))

    bytes cumulative_power
    if account_power_index:
        # Account type 2
        AccountPower last_account_power = get_account_power(user_address, account_power_index - 1)
        cumulative_power = last_account_power.cumulative_power
    else:
        # Account type 1
        cumulative_power = bzero(16)
    end

    # Crate a new account power
    AccountPower account_power = construct_account_power(bias, Global.LatestTimestamp, slope, cumulative_power)
    save_account_power(user_address, account_power_index, account_power)

    # Create checkpoint for Total Power
    create_checkpoints_internal(bias, slope, 1)
    # Schedule a future slope change for when the lock expires
    update_or_create_slope_change(lock_end_time, slope)
    increase_total_locked_amount(amount)

    # create_lock(address, locked_amount, lock_end_time)
    log(ARC28Event("create_lock(address,uint64,uint64)", user_address, box_extract(user_address, 0, 16)))
    return
end

@public()
func increase_lock_amount():
    # This method enables users to increase their currently locked token amount before the lock expiration time.

    bytes[32] user_address = Txn.Sender
    int amount = get_received_tiny_amount()

    # Amount must be at least the minimum increment
    assert(amount >= MIN_LOCK_AMOUNT_INCREMENT)

    # Get the current AccountState box (must already exist)
    box<AccountState> account_state = OpenBox(user_address)

    # Ensure that current lock is not expired.
    int lock_end_time = account_state.lock_end_time
    assert(lock_end_time > Global.LatestTimestamp)

    # Update locked_amount, power_count
    int old_locked_amount = account_state.locked_amount
    int new_locked_amount = old_locked_amount + amount
    account_state.locked_amount = new_locked_amount

    # Increase the power count by one.
    int account_power_index = account_state.power_count
    account_state.power_count = account_power_index + 1

    # Calculate the new slope & bias and the slope_delta & bias_delta 
    bytes old_locked_amount_slope = get_slope(old_locked_amount)
    bytes new_locked_amount_slope = get_slope(new_locked_amount)
    bytes slope_delta = new_locked_amount_slope b- old_locked_amount_slope

    int current_bias = get_bias(old_locked_amount_slope, (lock_end_time - Global.LatestTimestamp))
    int new_bias = get_bias(new_locked_amount_slope, (lock_end_time - Global.LatestTimestamp))
    int bias_delta = new_bias - current_bias

    # Create a new AccountPower based on the previous AccountPower
    AccountPower last_account_power = get_account_power(user_address, account_power_index - 1)
    int time_delta = Global.LatestTimestamp - last_account_power.timestamp

    # Using get_cumulative_power_1 is safe because the lock is not expired.
    bytes cumulative_power = last_account_power.cumulative_power b+ get_cumulative_power_1(last_account_power.bias, current_bias, time_delta)

    # Crate a new account power
    AccountPower account_power = construct_account_power(new_bias, Global.LatestTimestamp, new_locked_amount_slope, cumulative_power)
    save_account_power(user_address, account_power_index, account_power)

    # Create checkpoint for Total Power
    create_checkpoints_internal(bias_delta, slope_delta, 1)

    # Modify the future scheduled slope change
    update_or_create_slope_change(lock_end_time, slope_delta)
    increase_total_locked_amount(amount)

    # increase_lock_amount(address, locked_amount, lock_end_time, amount_delta)
    log(ARC28Event("increase_lock_amount(address,uint64,uint64,uint64)", user_address, box_extract(user_address, 0, 16), itob(amount)))
    return
end

@public()
func extend_lock_end_time(new_lock_end_time: int):
    # This method enables users to extend remaining lock duration.

    bytes[32] user_address = Txn.Sender

    # Get the current AccountState
    box<AccountState> account_state = OpenBox(user_address)
    int current_lock_end_time = account_state.lock_end_time
    # The lock must still be active
    assert(current_lock_end_time > Global.LatestTimestamp)

    # The new lock end time must be the start of a week
    assert(!(new_lock_end_time % WEEK))
    # The new duration must be no more than the max lock time
    assert(new_lock_end_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))
    # The new duration must be at least the minimum
    assert(new_lock_end_time >= (current_lock_end_time + (4 * WEEK)))
    # ensured indirectly
    # assert(new_lock_end_time > Global.LatestTimestamp)

    # Increase the power count by one.
    int account_power_index = account_state.power_count
    account_state.power_count = account_power_index + 1
    account_state.lock_end_time = new_lock_end_time

    # Calculate the slope, new bias and bias_delta
    bytes slope = get_slope(account_state.locked_amount)
    int current_bias = get_bias(slope, (current_lock_end_time - Global.LatestTimestamp))
    int new_bias = get_bias(slope, (new_lock_end_time - Global.LatestTimestamp))
    int bias_delta = new_bias - current_bias

    # Create a new AccountPower based on the previous AccountPower
    AccountPower last_account_power = get_account_power(user_address, account_power_index - 1)
    int time_delta = Global.LatestTimestamp - last_account_power.timestamp
    bytes cumulative_power = last_account_power.cumulative_power b+ get_cumulative_power_1(last_account_power.bias, current_bias, time_delta)

    # Create AccountPower
    AccountPower account_power = construct_account_power(new_bias, Global.LatestTimestamp, slope, cumulative_power)
    save_account_power(user_address, account_power_index, account_power)

    # Create checkpoint for Total Power
    create_checkpoints_internal(bias_delta, bzero(16), 1)
    # Revert the future scheduled slope change for the current lock end time
    revert_slope_change(current_lock_end_time, slope)
    # Schedule a new slope change for the new lock end time
    update_or_create_slope_change(new_lock_end_time, slope)

    # extend_lock_end_time(address, locked_amount, lock_end_time, lock_end_time_delta)
    log(ARC28Event("extend_lock_end_time(address,uint64,uint64,uint64)", user_address, box_extract(user_address, 0, 16), itob(new_lock_end_time - current_lock_end_time)))
    return
end

@public()
func withdraw():
    bytes[32] user_address = Txn.Sender

    box<AccountState> account_state = OpenBox(user_address)
    int locked_amount = account_state.locked_amount

    assert(locked_amount)
    # The lock must have expired
    assert(account_state.lock_end_time < Global.LatestTimestamp)

    # Create the final AccountPower recording the final state
    int account_power_index = account_state.power_count
    account_state.power_count = account_power_index + 1

    AccountPower last_account_power = get_account_power(user_address, account_power_index - 1)
    bytes cumulative_power = last_account_power.cumulative_power b+ get_cumulative_power_1(last_account_power.bias, 0, account_state.lock_end_time - last_account_power.timestamp)

    # Create AccountPower
    AccountPower account_power = construct_account_power(0, account_state.lock_end_time, bzero(16), cumulative_power)
    save_account_power(user_address, account_power_index, account_power)

    # Transfer the locked amount back to the user
    tiny_transfer(locked_amount, Global.CurrentApplicationAddress, user_address)
    decrease_total_locked_amount(locked_amount)

    # Reset the AccountState
    account_state.locked_amount = 0
    account_state.lock_end_time = 0

    # withdraw(user_address, amount)
    log(ARC28Event("withdraw(address,uint64)", user_address, itob(locked_amount)))
    return
end

@public()
func get_tiny_power_of(user_address: bytes[32]) int:
    # Current power can be calculated by using account state and account power.
    # The calculation is made using the account state to reduce required box references.
    int timestamp = Global.LatestTimestamp
    int tiny_power = 0

    int exists
    exists, _ = box_len(user_address)
    if exists:
        box<AccountState> account_state = Box(user_address)
        # There is only power if the lock has not expired
        if account_state.lock_end_time > timestamp:
            int remaining_time = account_state.lock_end_time - timestamp
            bytes slope = get_slope(account_state.locked_amount)
            tiny_power = get_bias(slope, remaining_time)
        end
    end

    return tiny_power
end

@public()
func get_tiny_power_of_at(user_address: bytes[32], timestamp: int, index: int) int:
    int tiny_power = 0
    assert(timestamp <= Global.LatestTimestamp)

    int exists
    exists, _ = box_len(user_address)
    if exists:
        box<AccountState> account_state = Box(user_address)
        # The index must be valid
        assert(index < account_state.power_count)

        # account_power.timestamp <= timestamp < next_account_power.timestamp
        AccountPower account_power = get_account_power(user_address, index)
        # Ensure that this AccountPower is before the timestamp
        assert(account_power.timestamp <= timestamp)
        # Ensure that this is the last AccountPower before the timestamp
        if (index + 1) < account_state.power_count:
            AccountPower next_account_power = get_account_power(user_address, index + 1)
            assert(timestamp < next_account_power.timestamp)
        end
        # Calculate the power using the AccountPower checkpoint and the calculated power between the checkpoint and the timestamp
        tiny_power = get_power(account_power.bias, account_power.slope, (timestamp - account_power.timestamp))
    end

    return tiny_power
end

@public()
func get_total_tiny_power() int:
    int timestamp = Global.LatestTimestamp
    # Get the latest TotalPower
    int index = app_global_get(TOTAL_POWER_COUNT_KEY) - 1
    TotalPower total_power = get_total_power(index)
    # Ensure that the latest checkpoint belongs to the same week so the slope is not changed.
    assert((total_power.timestamp / WEEK) == (timestamp / WEEK))

    # Calculate the power since the stored power
    int tiny_power = get_power(total_power.bias, total_power.slope, (timestamp - total_power.timestamp))
    return tiny_power
end

@public()
func get_total_tiny_power_at(timestamp: int, total_power_index: int) int:
    int tiny_power = 0
    # index must be valid
    assert(total_power_index < app_global_get(TOTAL_POWER_COUNT_KEY))
    assert(timestamp <= Global.LatestTimestamp)

    TotalPower total_power = get_total_power(total_power_index)
    # Ensure that the latest checkpoint belongs to the same week so the slope is not changed.
    assert((total_power.timestamp / WEEK) == (timestamp / WEEK))

    # Ensure that the TotalPower is before the timestamp
    if total_power.timestamp <= timestamp:
        # Ensure that the TotalPower is the last one before the timestamp
        if (total_power_index + 1) < app_global_get(TOTAL_POWER_COUNT_KEY):
            TotalPower next_total_power = get_total_power(total_power_index + 1)
            assert(timestamp < next_total_power.timestamp)
        end
        # Calculate the power since the stored power
        tiny_power = get_power(total_power.bias, total_power.slope, (timestamp - total_power.timestamp))
    else:
        # Index must be 0, it indicates that the given timestamp predates the first checkpoint.
        assert(!total_power_index)
    end

    return tiny_power
end

@public()
func get_account_cumulative_power_delta(user_address: bytes[32], timestamp_1: int, timestamp_2: int, account_power_index_1: int, account_power_index_2: int) bytes:
    bytes account_cumulative_power_1 = get_cumulative_power_of_at(user_address, timestamp_1, account_power_index_1)
    bytes account_cumulative_power_2 = get_cumulative_power_of_at(user_address, timestamp_2, account_power_index_2)
    bytes cumulative_power_delta = account_cumulative_power_2 b- account_cumulative_power_1
    # Pad the result to 16 bytes
    return bzero(16) b| cumulative_power_delta
end

@public()
func get_total_cumulative_power_delta(timestamp_1: int, timestamp_2: int, total_power_index_1: int, total_power_index_2: int) bytes:
    bytes total_cumulative_power_1 = get_total_cumulative_power_at(timestamp_1, total_power_index_1)
    bytes total_cumulative_power_2 = get_total_cumulative_power_at(timestamp_2, total_power_index_2)
    bytes cumulative_power_delta = total_cumulative_power_2 b- total_cumulative_power_1
    # Pad the result to 16 bytes
    return bzero(16) b| cumulative_power_delta
end

@public()
func get_box(box_name: bytes) bytes, bytes:
    bytes data
    _, data = box_get(box_name)
    # Return length as 2 bytes instead of using 8 bytes for uint64
    bytes length = extract(6, 2, itob(len(data)))
    return length, data
end

@public()
func delete_account_power_boxes(box_count: int):
    bytes[32] user_address = Txn.Sender

    box<AccountState> account_state = Box(user_address)
    int account_power_count = account_state.power_count
    int deleted_account_power_count = account_state.deleted_power_count
    int account_power_count_to_delete = box_count * ACCOUNT_POWER_BOX_ARRAY_LEN

    # keep at least 1 account power to able to track cumulative power delta
    assert(account_power_count > (deleted_account_power_count + account_power_count_to_delete))
    account_state.deleted_power_count = deleted_account_power_count + account_power_count_to_delete

    int box_index_start = deleted_account_power_count / ACCOUNT_POWER_BOX_ARRAY_LEN
    int box_index_end = box_index_start + box_count

    int min_balance_delta = 0
    for i in box_index_start:box_index_end:
        min_balance_delta = min_balance_delta + delete_box(Concat(user_address, itob(i)))
    end
    algo_transfer(min_balance_delta, Global.CurrentApplicationAddress, user_address)

    # address, box_index_start, box_count
    log(ARC28Event("delete_account_power_boxes(address,uint64,uint64)", user_address, itob(box_index_start), itob(box_count)))
    return
end

@public()
func delete_account_state():
    # Deletes remaining AccountPower and the AccountState boxes and returns the min balance Algo
    bytes[32] user_address = Txn.Sender

    box<AccountState> account_state = Box(user_address)
    int account_power_count = account_state.power_count
    # Ensure the lock is empty and expired
    assert(!account_state.locked_amount)
    assert(!account_state.lock_end_time)

    int deleted_account_power_count = account_state.deleted_power_count
    int box_index = deleted_account_power_count / ACCOUNT_POWER_BOX_ARRAY_LEN
    int box_count = 0

    int min_balance_delta = 0
    while account_power_count > deleted_account_power_count:
        # delete account power
        min_balance_delta = min_balance_delta + delete_box(Concat(user_address, itob(box_index)))

        box_count = box_count + 1
        box_index = box_index + 1
        deleted_account_power_count = deleted_account_power_count + ACCOUNT_POWER_BOX_ARRAY_LEN
    end

    # delete account state
    min_balance_delta = min_balance_delta + delete_box(user_address)
    algo_transfer(min_balance_delta, Global.CurrentApplicationAddress, user_address)

    # address, box_index_start, box_count
    log(ARC28Event("delete_account_state(address,uint64,uint64)", user_address, itob((box_index + 1) - box_count), itob(box_count)))
    return
end

func construct_account_power(bias: int, timestamp: int, slope: bytes, cumulative_power: bytes) AccountPower:
    return Cast(Concat(itob(bias), itob(timestamp), (bzero(16) b| slope), (bzero(16) b| cumulative_power)), AccountPower)
end

func save_account_power(address: bytes[32], index: int, data: AccountPower):
    int box_index = index / ACCOUNT_POWER_BOX_ARRAY_LEN
    int array_index = index % ACCOUNT_POWER_BOX_ARRAY_LEN
    bytes box_name = Concat(address, itob(box_index))
    _ = box_create(box_name, ACCOUNT_POWER_BOX_SIZE)
    box_replace(box_name, (array_index * ACCOUNT_POWER_SIZE), data)

    log(ARC28Event("account_power(address,uint64,uint64,uint64,uint128,uint128)", address, itob(index), data))
    return
end

func get_account_power(address: bytes[32], index: int) AccountPower:
    int box_index = index / ACCOUNT_POWER_BOX_ARRAY_LEN
    int array_index = index % ACCOUNT_POWER_BOX_ARRAY_LEN
    bytes box_name = Concat(address, itob(box_index))
    return Cast(box_extract(box_name, (array_index * ACCOUNT_POWER_SIZE), ACCOUNT_POWER_SIZE), AccountPower)
end

func construct_total_power(bias: int, timestamp: int, slope: bytes, cumulative_power: bytes) TotalPower:
    return Cast(Concat(itob(bias), itob(timestamp), (bzero(16) b| slope), (bzero(16) b| cumulative_power)), TotalPower)
end

func save_total_power(data: TotalPower):
    int index = app_global_get(TOTAL_POWER_COUNT_KEY)
    int box_index = index / TOTAL_POWER_BOX_ARRAY_LEN
    int array_index = index % TOTAL_POWER_BOX_ARRAY_LEN
    bytes box_name = Concat(TOTAL_POWER_BOX_PREFIX, itob(box_index))
    _ = box_create(box_name, TOTAL_POWER_BOX_SIZE)
    box_replace(box_name, (array_index * TOTAL_POWER_SIZE), data)

    log(ARC28Event("total_power(uint64,uint64,uint64,uint128,uint128)", itob(index), data))
    app_global_put(TOTAL_POWER_COUNT_KEY, index + 1)
    app_global_put(LAST_TOTAL_POWER_TIMESTAMP, extract_uint64(data, 8))
    return
end

func get_total_power(index: int) TotalPower:
    int box_index = index / TOTAL_POWER_BOX_ARRAY_LEN
    int array_index = index % TOTAL_POWER_BOX_ARRAY_LEN
    bytes box_name = Concat(TOTAL_POWER_BOX_PREFIX, itob(box_index))
    return Cast(box_extract(box_name, (array_index * TOTAL_POWER_SIZE), TOTAL_POWER_SIZE), TotalPower)
end

@public()
func get_cumulative_power_of_at(user_address: bytes[32], timestamp: int, account_power_index: int) bytes:
    # Calculates the cumulative power of a user at a specific timestamp in the past
    bytes cumulative_power
    assert(timestamp <= Global.LatestTimestamp)

    bytes cumulative_power_delta
    int exists
    exists, _ = box_len(user_address)
    if exists:
        box<AccountState> account_state = Box(user_address)
        assert(account_power_index < account_state.power_count)

        # account_power.timestamp <= timestamp < next_account_power.timestamp
        AccountPower account_power = get_account_power(user_address, account_power_index)
        if account_power.timestamp <= timestamp:
            # assert(timestamp > account_power.timestamp)
            if (account_power_index + 1) < account_state.power_count:
                AccountPower next_account_power = get_account_power(user_address, account_power_index + 1)
                assert(timestamp < next_account_power.timestamp)
            end

            int time_delta = timestamp - account_power.timestamp
            int bias_delta = get_bias(account_power.slope, time_delta)
            if bias_delta < account_power.bias:
                cumulative_power_delta = get_cumulative_power_1(account_power.bias, (account_power.bias - bias_delta), time_delta)
            else:
                cumulative_power_delta = get_cumulative_power_2(account_power.bias, account_power.slope)
            end

            cumulative_power = bzero(16) b| (account_power.cumulative_power b+ cumulative_power_delta)
        else:
            # Index must be 0, it indicates that the given timestamp predates the first checkpoint.
            assert(!account_power_index)
            cumulative_power = bzero(16)
        end
    end

    return cumulative_power
end

@public()
func get_total_cumulative_power_at(timestamp: int, total_power_index: int) bytes:
    # Calculates the cumulative total power at a specific timestamp in the past
    bytes total_cumulative_power
    assert(total_power_index < app_global_get(TOTAL_POWER_COUNT_KEY))
    assert(timestamp <= Global.LatestTimestamp)

    bytes cumulative_power_delta
    TotalPower total_power = get_total_power(total_power_index)

    if total_power.timestamp <= timestamp:
        if (total_power_index + 1) < app_global_get(TOTAL_POWER_COUNT_KEY):
            TotalPower next_total_power = get_total_power(total_power_index + 1)
            assert(timestamp < next_total_power.timestamp)
        end

        # Ensure that the latest checkpoint belongs to the same week so the slope is not changed.
        assert((total_power.timestamp / WEEK) == (timestamp / WEEK))

        int time_delta = timestamp - total_power.timestamp
        int bias_delta = get_bias(total_power.slope, time_delta)
        if bias_delta < total_power.bias:
            cumulative_power_delta = get_cumulative_power_1(total_power.bias, (total_power.bias - bias_delta), time_delta)
        else:
            cumulative_power_delta = get_cumulative_power_2(total_power.bias, total_power.slope)
        end

        total_cumulative_power = bzero(16) b| (total_power.cumulative_power b+ cumulative_power_delta)
    else:
        # Index must be 0, it indicates that the given timestamp predates the first checkpoint.
        assert(!total_power_index)
        total_cumulative_power = bzero(16)
    end

    return total_cumulative_power
end

func create_checkpoints_internal(account_bias_delta: int, account_slope_delta: bytes, has_account_event: int):
    # https://github.com/curvefi/curve-dao-contracts/blob/master/contracts/VotingEscrow.vy#L234

    TotalPower last_total_power = get_total_power(app_global_get(TOTAL_POWER_COUNT_KEY) - 1)

    int bias = last_total_power.bias
    bytes slope = last_total_power.slope
    bytes cumulative_power = last_total_power.cumulative_power

    int last_checkpoint_timestamp = last_total_power.timestamp
    int temp_checkpoint_timestamp = (last_checkpoint_timestamp / WEEK) * WEEK

    bytes slope_delta
    int bias_delta
    int time_delta

    int i = 0
    int stop = 9
    while i < stop:
        temp_checkpoint_timestamp = temp_checkpoint_timestamp + WEEK
        slope_delta = itob(0)

        if temp_checkpoint_timestamp > Global.LatestTimestamp:
            temp_checkpoint_timestamp = Global.LatestTimestamp
        end

        # If it is a new week, get the slope_delta from the slope_change box.
        if !(temp_checkpoint_timestamp % WEEK):
            _, slope_delta = box_get(Concat(SLOPE_CHANGE_BOX_PREFIX, itob(temp_checkpoint_timestamp)))
        end

        time_delta = temp_checkpoint_timestamp - last_checkpoint_timestamp
        bias_delta = get_bias(slope, time_delta)

        if bias_delta > bias:
            cumulative_power = cumulative_power b+ get_cumulative_power_2(bias, slope)
            slope = bzero(16)
            bias = 0
        else:
            cumulative_power = cumulative_power b+ get_cumulative_power_1(bias, (bias - bias_delta), time_delta)
            bias = bias - bias_delta
        end

        if slope_delta b>= slope:
            slope = bzero(16)
            bias = 0
        else:
            slope = slope b- slope_delta
        end

        last_checkpoint_timestamp = temp_checkpoint_timestamp

        # If it has arrived to the current time, break the loop.
        if temp_checkpoint_timestamp == Global.LatestTimestamp:
            i = stop
            if has_account_event:
                save_total_power(construct_total_power(bias + account_bias_delta, last_checkpoint_timestamp, slope b+ account_slope_delta, cumulative_power))
            else:
                save_total_power(construct_total_power(bias, last_checkpoint_timestamp, slope, cumulative_power))
            end
        else:
            i = i + 1
            save_total_power(construct_total_power(bias, last_checkpoint_timestamp, slope, cumulative_power))
        end
    end
    assert(!(has_account_event && (temp_checkpoint_timestamp != Global.LatestTimestamp)))
    return
end

func get_cumulative_power_1(old_bias: int, new_bias: int, time_delta: int) bytes:
    # (old_bias + new_bias) * time_delta / 2
    return ((itob(old_bias) b+ itob(new_bias)) b* itob(time_delta)) b/ itob(2)
end

func get_cumulative_power_2(old_bias: int, slope: bytes) bytes:
    # time_delta = old_bias / slope
    # (old_bias + new_bias) * (old_bias / slope) / 2, new_bias = 0
    # (old_bias * old_bias) / (slope * 2)
    if slope != bzero(16):
        return ((itob(old_bias) b* itob(old_bias)) b* TWO_TO_THE_64) b/ (slope b* itob(2))
    end
    return bzero(16)
end

func get_bias(slope: bytes, time: int) int:
    # 87bits * 32bits / 64bit = 55bits
    # (11bytes * 4bytes) / 8bytes = 7bytes
    return btoi((slope b* itob(time)) b/ TWO_TO_THE_64)
end

func get_slope(locked_amount: int) bytes:
    # max locked_amount = total supply = 1e15 = 50bits = 7bytes
    # (50bits * 64bits) / 27bits = 87 bits = 11bytes
    return (itob(locked_amount) b* TWO_TO_THE_64) b/ itob(MAX_LOCK_TIME)
end

func revert_slope_change(timestamp: int, slope: bytes):
    bytes box_name = Concat(SLOPE_CHANGE_BOX_PREFIX, itob(timestamp))
    bytes new_slope = bzero(16) b| (box_extract(box_name, 0, 16) b- slope)
    box_put(box_name, new_slope)

    log(ARC28Event("slope_change(uint64,uint128)", itob(timestamp), new_slope))
    return
end

func update_or_create_slope_change(timestamp: int, slope: bytes):
    bytes box_name = Concat(SLOPE_CHANGE_BOX_PREFIX, itob(timestamp))
    bytes slope_delta
    _, slope_delta = box_get(box_name)
    bytes new_slope = bzero(16) b| (slope_delta b+ slope)
    box_put(box_name, new_slope)

    log(ARC28Event("slope_change(uint64,uint128)", itob(timestamp), new_slope))
    return
end

func get_power(bias: int, slope: bytes, time_delta: int) int:
    int bias_delta = get_bias(slope, time_delta)
    if bias_delta < bias:
        return bias - bias_delta
    end
    return 0
end

func increase_total_locked_amount(amount: int):
    app_global_put(TOTAL_LOCKED_AMOUNT_KEY, app_global_get(TOTAL_LOCKED_AMOUNT_KEY) + amount)
    return
end

func decrease_total_locked_amount(amount: int):
    app_global_put(TOTAL_LOCKED_AMOUNT_KEY, app_global_get(TOTAL_LOCKED_AMOUNT_KEY) - amount)
    return
end

func get_received_tiny_amount() int:
    # this function is designed to retrieve the amount of 'TINY' tokens that have been transferred from a user's account to the contract's account.
    # the asset transfer transaction must be positioned at the -1 index in the transaction group.

    assert(Txn.GroupIndex)
    int input_txn_index = Txn.GroupIndex - 1
    assert(Gtxn[input_txn_index].Sender == Txn.Sender)
    assert(Gtxn[input_txn_index].TypeEnum == Axfer)
    assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
    assert(Gtxn[input_txn_index].XferAsset == app_global_get(TINY_ASSET_ID_KEY))
    return Gtxn[input_txn_index].AssetAmount
end

func delete_box(box_name: bytes) int:
    int initial_min_balance = min_balance(Global.CurrentApplicationAddress)
    assert(box_del(box_name))
    int final_min_balance = min_balance(Global.CurrentApplicationAddress)
    int min_balance_delta = initial_min_balance - final_min_balance

    log(ARC28Event("box_del(byte[])", extract(6, 2, itob(len(box_name))), box_name))
    return min_balance_delta
end

func algo_transfer(amount: int, sender: bytes, receiver: bytes):
    inner_txn:
        TypeEnum: Pay
        Sender: sender
        Receiver: receiver
        Amount: amount
        Fee: 0
    end
    return
end

func tiny_transfer(amount: int, sender: bytes, receiver: bytes):
    inner_txn:
        TypeEnum: Axfer
        Sender: sender
        AssetReceiver: receiver
        AssetAmount: amount
        XferAsset: app_global_get(TINY_ASSET_ID_KEY)
        Fee: 0
    end
    return
end
