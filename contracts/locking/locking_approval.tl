#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

# Name: <address>
# Size: 24 bytes
struct AccountState:
    locked_amount: int
    lock_end_time: int
    power_count: int
end

# Name: <address><box_index><array_index>
# Size: 48 bytes (8+8+16+16). 1 Box(1008) = 21 AccountPower
struct AccountPower:
    bias: int
    timestamp: int
    slope: bytes[16]
    cumulative_power: bytes[16]
end

# Name: TOTAL_POWERS<box_index><array_index>
# Size: 48 bytes (8+8+16+16). 1 Box(1008) = 21
struct TotalPower:
    bias: int
    timestamp: int
    slope: bytes[16]
    cumulative_power: bytes[16]
end

# SLOPE_CHANGES<timestamp>
# 16 bytes
struct SlopeChange:
    slope_delta: bytes[16]
end

# 2^64 - 1
# const int MAX_UINT64 = 18446744073709551615
# 24 * 60 * 60
const int DAY = 86400
# 7 * DAY
const int WEEK = 604800
# 4 * 365 * DAY
const int MAX_LOCK_TIME = 126144000
const int MIN_LOCK_AMOUNT = 10000000
const int MIN_LOCK_INCREMENT = 10000000
const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes TOTAL_POWERS = "total_powers"
const bytes SLOPE_CHANGES = "slope_changes"

const int ACCOUNT_STATE_SIZE = 24
const int SLOPE_CHANGE_SIZE = 16

const int ACCOUNT_POWER_SIZE = 48
const int ACCOUNT_POWER_BOX_SIZE = 1008
const int ACCOUNT_POWER_BOX_ARRAY_LEN = 21

const int TOTAL_POWER_SIZE = 48
const int TOTAL_POWER_BOX_SIZE = 1008
const int TOTAL_POWER_BOX_ARRAY_LEN = 21

# Keys
const bytes TINY_ASSET_ID_KEY = "tiny_asset_id"
const bytes TOTAL_LOCKED_AMOUNT_KEY = "total_locked_amount"
const bytes TOTAL_POWER_COUNT_KEY = "total_power_count"
const bytes CREATION_TIMESTAMP_KEY = "creation_timestamp"

if !Txn.ApplicationID:
    jump create_app
end

switch Txn.OnCompletion:
    NoOp: main
    OptIn: fail
    CloseOut: fail
    UpdateApplication: fail
    DeleteApplication: fail
end

block fail:
    exit(0)
end

block create_app:
    app_global_put(TINY_ASSET_ID_KEY, Txn.Assets[0])
    app_global_put(TOTAL_LOCKED_AMOUNT_KEY, 0)
    app_global_put(TOTAL_POWER_COUNT_KEY, 0)
    app_global_put(CREATION_TIMESTAMP_KEY, Global.LatestTimestamp)
    exit(1)
end

# TODO: Try adding support increase & extend lock with single app call.
block main:
    switch Txn.ApplicationArgs[0]:
        "create_lock": create_lock
        "increase_lock_amount": increase_lock_amount
        "extend_lock_end_time": extend_lock_end_time
        "create_checkpoints": create_checkpoints
        "get_tiny_power_of": get_tiny_power_of
        "get_tiny_power_of_at": get_tiny_power_of_at
        "get_cumulative_power_of_at": get_cumulative_power_of_at
        "get_total_tiny_power": get_total_tiny_power
        "get_total_tiny_power_at": get_total_tiny_power_at
        "get_total_cumulative_power_at": get_total_cumulative_power_at
        "get_cumulative_powers_for_rewards": get_cumulative_powers_for_rewards
        "increase_budget": increase_budget
        "withdraw": withdraw
        "init": init
    end

    block increase_budget:
        # Box read budget and opcode budget will be incread using this method.
        exit(1)
    end

    block init:
        # create the first total power
        assert(!app_global_get(TOTAL_POWER_COUNT_KEY))
        bytes total_power = construct_total_power(0, Global.LatestTimestamp, bzero(16), bzero(16))
        save_total_power(total_power)

        # opt-in to TINY
        transfer(app_global_get(TINY_ASSET_ID_KEY), 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        exit(1)
    end

    block create_checkpoints:
        create_checkpoints(0, bzero(16))
        exit(1)
    end

    block create_lock:
        # inputs
        bytes user_address = Txn.Sender
        int amount = get_received_tiny_amount()
        int lock_end_time = btoi(Txn.ApplicationArgs[1])

        # validations
        assert(amount >= MIN_LOCK_AMOUNT)

        assert(!(lock_end_time % WEEK))
        assert(lock_end_time >= (Global.LatestTimestamp + WEEK))
        assert(lock_end_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))

        # user can lock withdraw and lock again
        _ = box_create(user_address, ACCOUNT_STATE_SIZE)
        box<AccountState> account_state = Box(user_address)
        # There must be no locked amount
        assert(!account_state.locked_amount)
        account_state.locked_amount = amount
        account_state.lock_end_time = lock_end_time
        int account_power_index = account_state.power_count
        account_state.power_count = account_power_index + 1

        bytes slope = get_slope(amount)
        int bias = get_bias(slope, (lock_end_time - Global.LatestTimestamp))

        bytes cumulative_power
        if account_power_index:
            AccountPower latest_account_power = get_account_power(user_address, account_power_index - 1)
            cumulative_power = latest_account_power.cumulative_power
        else:
            cumulative_power =  bzero(16)
        end

        bytes account_power = construct_account_power(bias, Global.LatestTimestamp, slope, cumulative_power)
        save_account_power(user_address, account_power_index, account_power)

        create_checkpoints(bias, slope)
        update_or_create_slope_change(lock_end_time, slope)
        increase_total_locked_amount(amount)
        exit(1)
    end

    block increase_lock_amount:
        # inputs
        bytes user_address = Txn.Sender
        int amount = get_received_tiny_amount()

        # validations
        assert(amount >= MIN_LOCK_INCREMENT)

        # Get AccountState
        box<AccountState> account_state = OpenBox(user_address)
        int lock_end_time = account_state.lock_end_time
        assert(lock_end_time > Global.LatestTimestamp)

        # Update locked_amount, power_count
        int old_locked_amount = account_state.locked_amount
        int new_locked_amount = old_locked_amount + amount
        account_state.locked_amount = new_locked_amount

        int account_power_index = account_state.power_count
        account_state.power_count = account_power_index + 1

        bytes old_slope = get_slope(old_locked_amount)
        bytes new_slope = get_slope(new_locked_amount)
        bytes slope_delta = new_slope b- old_slope

        int old_bias = get_bias(old_slope, (lock_end_time - Global.LatestTimestamp))
        int new_bias = get_bias(new_slope, (lock_end_time - Global.LatestTimestamp))
        int bias_delta = new_bias - old_bias

        AccountPower latest_account_power = get_account_power(user_address, account_power_index - 1)
        int time_delta = Global.LatestTimestamp - latest_account_power.timestamp
        bytes cumulative_power = latest_account_power.cumulative_power b+ get_cumulative_power_1(latest_account_power.bias, old_bias, time_delta)

        # Create AccountPower
        bytes account_power = construct_account_power(new_bias, Global.LatestTimestamp, new_slope, cumulative_power)
        save_account_power(user_address, account_power_index, account_power)

        create_checkpoints(bias_delta, slope_delta)
        update_or_create_slope_change(lock_end_time, slope_delta)
        increase_total_locked_amount(amount)
        exit(1)
    end

    block extend_lock_end_time:
        #inputs
        bytes user_address = Txn.Sender
        int new_lock_end_time = btoi(Txn.ApplicationArgs[1])

        # validations
        assert(!(new_lock_end_time % WEEK))
        assert(new_lock_end_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))

        # Get AccountState
        box<AccountState> account_state = OpenBox(user_address)
        int old_lock_end_time = account_state.lock_end_time
        assert(old_lock_end_time > Global.LatestTimestamp)
        assert(new_lock_end_time > old_lock_end_time)

        # these are indirectly satisfied
        # assert(new_lock_end_time >= (old_lock_end_time + WEEK))
        # assert(new_lock_end_time > Global.LatestTimestamp)

        # Update lock_end_time, power_count
        int account_power_index = account_state.power_count
        account_state.lock_end_time = new_lock_end_time
        account_state.power_count = account_power_index + 1

        bytes slope = get_slope(account_state.locked_amount)
        int old_bias = get_bias(slope, (old_lock_end_time - Global.LatestTimestamp))
        int new_bias = get_bias(slope, (new_lock_end_time - Global.LatestTimestamp))
        int bias_delta = new_bias - old_bias

        AccountPower latest_account_power = get_account_power(user_address, account_power_index - 1)
        int time_delta = Global.LatestTimestamp - latest_account_power.timestamp
        bytes cumulative_power = latest_account_power.cumulative_power b+ get_cumulative_power_1(latest_account_power.bias, old_bias, time_delta)

        # Create AccountPower
        bytes account_power = construct_account_power(new_bias, Global.LatestTimestamp, slope, cumulative_power)
        save_account_power(user_address, account_power_index, account_power)

        create_checkpoints(bias_delta, itob(0))
        revert_slope_change(old_lock_end_time, slope)
        update_or_create_slope_change(new_lock_end_time, slope)
        exit(1)
    end

    block get_tiny_power_of:
        # Current power can be calculated by using account state and account power.
        # The calculation is made using the account state to reduce required box references.
        bytes user_address = Txn.Accounts[1]
        int timestamp = Global.LatestTimestamp
        int tiny_power = 0

        int exists
        exists, _ = box_len(user_address)
        if exists:
            box<AccountState> account_state = Box(user_address)
            if account_state.lock_end_time > timestamp:
                int remaining_time = account_state.lock_end_time - timestamp
                bytes slope = get_slope(account_state.locked_amount)
                tiny_power = get_bias(slope, remaining_time)
            end
        end

        log(itob(tiny_power))
        exit(1)
    end

    block get_tiny_power_of_at:
        bytes user_address = Txn.Accounts[1]
        int timestamp = btoi(Txn.ApplicationArgs[1])
        int index = btoi(Txn.ApplicationArgs[2])
        int tiny_power = 0
        assert(timestamp <= Global.LatestTimestamp)

        int exists
        exists, _ = box_len(user_address)
        if exists:
            box<AccountState> account_state = Box(user_address)
            assert(index < account_state.power_count)

            # account_power.timestamp <= timestamp < next_account_power.timestamp
            AccountPower account_power = get_account_power(user_address, index)
            assert(account_power.timestamp <= timestamp)
            if (index + 1) < account_state.power_count:
                AccountPower next_account_power = get_account_power(user_address, index + 1)
                assert(timestamp < next_account_power.timestamp)
            end

            tiny_power = get_power(account_power.bias, account_power.slope, (timestamp - account_power.timestamp))
        end

        log(itob(tiny_power))
        exit(1)
    end

    block get_cumulative_power_of_at:
        bytes user_address = Txn.Accounts[1]
        int timestamp = btoi(Txn.ApplicationArgs[1])
        int account_power_index = btoi(Txn.ApplicationArgs[2])

        bytes cumulative_power =  get_cumulative_power_of_at(user_address, timestamp, account_power_index)
        log(cumulative_power)
        exit(1)
    end
    
    block get_total_tiny_power:
        int timestamp = Global.LatestTimestamp
        int index = app_global_get(TOTAL_POWER_COUNT_KEY) - 1

        TotalPower total_power = get_total_power(index)
        # Ensure that the latest checkpoint belongs to the same week so the slope is not changed.
        assert((total_power.timestamp / WEEK) == (timestamp / WEEK))

        int tiny_power = get_power(total_power.bias, total_power.slope, (timestamp - total_power.timestamp))
        log(itob(tiny_power))
        exit(1)
    end

    block get_total_tiny_power_at:
        int timestamp = btoi(Txn.ApplicationArgs[1])
        int index = btoi(Txn.ApplicationArgs[2])
        int tiny_power = 0
        assert(index < app_global_get(TOTAL_POWER_COUNT_KEY))
        assert(timestamp <= Global.LatestTimestamp)

        TotalPower total_power = get_total_power(index)
        if total_power.timestamp <= timestamp:
            # assert(total_power.timestamp <= timestamp)
            # Ensure that the latest checkpoint belongs to the same week so the slope is not changed.
            assert((total_power.timestamp / WEEK) == (timestamp / WEEK))

            if (index + 1) < app_global_get(TOTAL_POWER_COUNT_KEY):
                TotalPower next_total_power = get_total_power(index + 1)
                assert(timestamp < next_total_power.timestamp)
            end

            tiny_power = get_power(total_power.bias, total_power.slope, (timestamp - total_power.timestamp))
        else:
            # Index must be 0, it indicates that the given timestamp predates the first checkpoint.
            assert(!index)
        end

        log(itob(tiny_power))
        exit(1)
    end

    block get_total_cumulative_power_at:
        int timestamp = btoi(Txn.ApplicationArgs[1])
        int total_power_index = btoi(Txn.ApplicationArgs[2])
        bytes total_cumulative_power = get_total_cumulative_power_at(timestamp, total_power_index)

        log(total_cumulative_power)
        exit(1)
    end

    block get_cumulative_powers_for_rewards:
        bytes user_address = Txn.Accounts[1]
        int timestamp_1 = btoi(Txn.ApplicationArgs[1])
        int timestamp_2 = btoi(Txn.ApplicationArgs[2])
        int account_power_index_1 = btoi(Txn.ApplicationArgs[3])
        int account_power_index_2 = btoi(Txn.ApplicationArgs[4])
        int total_power_index_1 = btoi(Txn.ApplicationArgs[5])
        int total_power_index_2 = btoi(Txn.ApplicationArgs[6])

        bytes cumulative_power_1 = get_cumulative_power_of_at(user_address, timestamp_1, account_power_index_1)
        bytes cumulative_power_2 = get_cumulative_power_of_at(user_address, timestamp_2, account_power_index_2)

        bytes total_cumulative_power_1 = get_total_cumulative_power_at(timestamp_1, total_power_index_1)
        bytes total_cumulative_power_2 = get_total_cumulative_power_at(timestamp_2, total_power_index_2)

        log(concat(concat(concat(cumulative_power_1, cumulative_power_2), total_cumulative_power_1), total_cumulative_power_2))
        exit(1)
    end

    block withdraw:
        bytes user_address = Txn.Sender

        box<AccountState> account_state = OpenBox(user_address)
        int locked_amount = account_state.locked_amount

        assert(locked_amount)
        assert(account_state.lock_end_time < Global.LatestTimestamp)

        int account_power_index = account_state.power_count
        account_state.power_count = account_power_index + 1

        AccountPower latest_account_power = get_account_power(user_address, account_power_index - 1)
        bytes cumulative_power = latest_account_power.cumulative_power b+ get_cumulative_power_2(latest_account_power.bias, latest_account_power.slope)

        # Create AccountPower
        bytes account_power = construct_account_power(0, account_state.lock_end_time, bzero(16), cumulative_power)
        save_account_power(user_address, account_power_index, account_power)

        transfer(app_global_get(TINY_ASSET_ID_KEY), locked_amount, Global.CurrentApplicationAddress, user_address)
        decrease_total_locked_amount(locked_amount)

        account_state.locked_amount = 0
        account_state.lock_end_time = 0
        exit(1)
    end
end

func construct_account_power(bias: int, timestamp: int, slope: bytes, cumulative_power: bytes) bytes:
    return concat(concat(concat(itob(bias), itob(timestamp)), (bzero(16) b| slope)), (bzero(16) b| cumulative_power))
end

func save_account_power(address: bytes, index: int, data: bytes):
    assert(len(data) == ACCOUNT_POWER_SIZE)
    int box_index = index / ACCOUNT_POWER_BOX_ARRAY_LEN
    int array_index = index % ACCOUNT_POWER_BOX_ARRAY_LEN
    bytes box_name = concat(address, itob(box_index))
    _ = box_create(box_name, ACCOUNT_POWER_BOX_SIZE)
    box_replace(box_name, (array_index * ACCOUNT_POWER_SIZE), data)
    return
end

func get_account_power(address: bytes, index: int) bytes:
    int box_index = index / ACCOUNT_POWER_BOX_ARRAY_LEN
    int array_index = index % ACCOUNT_POWER_BOX_ARRAY_LEN
    bytes box_name = concat(address, itob(box_index))
    return box_extract(box_name, (array_index * ACCOUNT_POWER_SIZE), ACCOUNT_POWER_SIZE)
end

func construct_total_power(bias: int, timestamp: int, slope: bytes, cumulative_power: bytes) bytes:
    return concat(concat(concat(itob(bias), itob(timestamp)), (bzero(16) b| slope)), (bzero(16) b| cumulative_power))
end

func save_total_power(data: bytes):
    int index = app_global_get(TOTAL_POWER_COUNT_KEY)

    assert(len(data) == TOTAL_POWER_SIZE)
    int box_index = index / TOTAL_POWER_BOX_ARRAY_LEN
    int array_index = index % TOTAL_POWER_BOX_ARRAY_LEN
    bytes box_name = concat(TOTAL_POWERS, itob(box_index))
    _ = box_create(box_name, TOTAL_POWER_BOX_SIZE)
    box_replace(box_name, (array_index * TOTAL_POWER_SIZE), data)

    app_global_put(TOTAL_POWER_COUNT_KEY, index + 1)
    return
end

func get_total_power(index: int) bytes:
    int box_index = index / TOTAL_POWER_BOX_ARRAY_LEN
    int array_index = index % TOTAL_POWER_BOX_ARRAY_LEN
    bytes box_name = concat(TOTAL_POWERS, itob(box_index))
    return box_extract(box_name, (array_index * TOTAL_POWER_SIZE), TOTAL_POWER_SIZE)
end

func get_cumulative_power_of_at(user_address: bytes, timestamp: int, account_power_index: int) bytes:
    bytes cumulative_power = bzero(16)
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
        end
    end

    return cumulative_power
end

func get_total_cumulative_power_at(timestamp: int, total_power_index: int) bytes:
    bytes total_cumulative_power
    assert(total_power_index < app_global_get(TOTAL_POWER_COUNT_KEY))
    assert(timestamp <= Global.LatestTimestamp)

    bytes cumulative_power_delta
    TotalPower total_power = get_total_power(total_power_index)
    if total_power.timestamp <= timestamp:
        # assert(total_power.timestamp <= timestamp)
        # Ensure that the latest checkpoint belongs to the same week so the slope is not changed.
        assert((total_power.timestamp / WEEK) == (timestamp / WEEK))

        if (total_power_index + 1) < app_global_get(TOTAL_POWER_COUNT_KEY):
            TotalPower next_total_power = get_total_power(total_power_index + 1)
            assert(timestamp < next_total_power.timestamp)
        end

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

func create_checkpoints(account_bias_delta: int, account_slope_delta: bytes):
    # https://github.com/curvefi/curve-dao-contracts/blob/master/contracts/VotingEscrow.vy#L234

    int has_account_event = account_bias_delta || (account_slope_delta != bzero(16))
    TotalPower last_total_power = get_total_power(app_global_get(TOTAL_POWER_COUNT_KEY) - 1)

    int bias = last_total_power.bias
    int last_checkpoint = last_total_power.timestamp
    bytes slope = last_total_power.slope
    bytes cumulative_power = last_total_power.cumulative_power

    int t_i = (last_checkpoint / DAY) * DAY
    bytes d_slope
    int d_bias
    int time_delta

    int i = 0
    int stop = 7
    while i < stop:
        t_i = t_i + DAY
        d_slope = itob(0)

        if t_i > Global.LatestTimestamp:
            t_i = Global.LatestTimestamp
        end
        if !(t_i % WEEK):
            _, d_slope = box_get(concat(SLOPE_CHANGES, itob(t_i)))
        end

        time_delta = t_i - last_checkpoint
        d_bias = get_bias(slope, time_delta)
        if d_bias > bias:
            cumulative_power = cumulative_power b+ get_cumulative_power_2(bias, slope)
            slope = bzero(16)
            bias = 0
        else:
            cumulative_power = cumulative_power b+ get_cumulative_power_1(bias, (bias - d_bias), time_delta)
            bias = bias - d_bias
        end

        if d_slope b>= slope:
            slope = bzero(16)
            bias = 0
        else:
            slope = slope b- d_slope
        end

        last_checkpoint = t_i
        if t_i == Global.LatestTimestamp:
            i = stop
            if has_account_event:
                save_total_power(construct_total_power(bias + account_bias_delta, last_checkpoint, slope b+ account_slope_delta, cumulative_power))
            else:
                save_total_power(construct_total_power(bias, last_checkpoint, slope, cumulative_power))
            end
        else:
            i = i + 1
            save_total_power(construct_total_power(bias, last_checkpoint, slope, cumulative_power))
        end
    end
    assert(!(has_account_event && (t_i != Global.LatestTimestamp)))
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
    return btoi((slope b* itob(time)) b/ TWO_TO_THE_64)
end

func get_slope(locked_amount: int) bytes:
    return (itob(locked_amount) b* TWO_TO_THE_64) b/ itob(MAX_LOCK_TIME)
end

func revert_slope_change(timestamp: int, slope: bytes):
    # box<SlopeChange> slope_change = OpenBox(concat(SLOPE_CHANGES, itob(timestamp)))
    # slope_change.slope_delta = bzero(16) b| (slope_change.slope_delta b- slope)
    bytes box_name = concat(SLOPE_CHANGES, itob(timestamp))
    box_put(box_name, (bzero(16) b| (box_extract(box_name, 0, 16) b- slope)))
    return
end

func update_or_create_slope_change(timestamp: int, slope: bytes):
    bytes box_name = concat(SLOPE_CHANGES, itob(timestamp))
    # int exists
    # exists, _ = box_len(box_name)
    # if exists:
    #     box<SlopeChange> slope_change = OpenBox(box_name)
    #     slope_change.slope_delta = bzero(16) b| (slope_change.slope_delta b+ slope)
    # else:
    #     box<SlopeChange> new_slope_change = CreateBox(box_name)
    #     new_slope_change.slope_delta = bzero(16) b| slope
    # end
    bytes slope_delta
    _, slope_delta = box_get(box_name)
    box_put(box_name, (bzero(16) b| (slope_delta b+ slope)))
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
    assert(Txn.GroupIndex)
    int input_txn_index = Txn.GroupIndex - 1
    assert(Gtxn[input_txn_index].Sender == Txn.Sender)
    assert(Gtxn[input_txn_index].TypeEnum == Axfer)
    assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
    assert(Gtxn[input_txn_index].XferAsset == app_global_get(TINY_ASSET_ID_KEY))
    return Gtxn[input_txn_index].AssetAmount
end

func transfer(asset_id: int, amount: int, sender: bytes, receiver: bytes):
    # This function is copied from Tinyman AMM Contracts V2.
    # "asset_id == 0" is updated as "!asset_id" for budget optimization.
    # https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/amm_approval.tl#L1146

    if !asset_id:
        inner_txn:
            TypeEnum: Pay
            Sender: sender
            Receiver: receiver
            Amount: amount
            Fee: 0
        end
    else:
        inner_txn:
            TypeEnum: Axfer
            Sender: sender
            AssetReceiver: receiver
            AssetAmount: amount
            XferAsset: asset_id
            Fee: 0
        end
    end
    return
end
