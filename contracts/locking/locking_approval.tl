#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

struct AccountState:
    locked_amount: int
    lock_end_time: int
    index: int
end

# Account power is required for rewards

# 64 bytes (8+8+16+32). 1 Box(1024) = 16 AccountPower
struct AccountPower:
    bias: int
    timestamp: int
    slope: bytes[16]
    delegatee: bytes[32]
end

# Account total power is required for voting power
# struct AccountVotingPower:
#     bias: int
#     timestamp: int
#     slope: bytes[16]
# end

# 48 bytes (8+8+16+16). 1 Box(1008) = 21
struct TotalPower:
    bias: int
    timestamp: int
    slope: bytes[16]
    cumulative_power: bytes[16]
end

struct SlopeChange:
    slope_delta: bytes[16]
end

# 32K bytes 
const int MAX_BOX_SIZE = 32768
# 2^64 - 1
const int MAX_UINT64 = 18446744073709551615
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

const int TOTAL_POWER_SIZE = 48
const int TOTAL_POWER_BOX_SIZE = 1008
const int TOTAL_POWER_BOX_ARRAY_LEN = 21

const int ACCOUNT_POWER_SIZE = 64
const int ACCOUNT_POWER_BOX_SIZE = 1024
const int ACCOUNT_POWER_BOX_ARRAY_LEN = 16

# Keys
const bytes TINY_ASSET_ID_KEY = "tiny_asset_id"
const bytes TOTAL_LOCKED_AMOUNT_KEY = "total_locked_amount"
const bytes TOTAL_POWER_COUNT_KEY = "total_power_count"

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
    exit(1)
end

# TODO: Asset opt-in TINY
# TODO: Try adding support increase & extend lock with single app call.
# TODO: Try creating checkpoint at the beginning of other methods.
# TODO: Add delegatee support.
# TODO: Allow deleting old boxes (Account Power).

block main:
    switch Txn.ApplicationArgs[0]:
        "create_lock": create_lock
        "increase_lock_amount": increase_lock_amount
        "extend_lock_end_time": extend_lock_end_time
        "create_checkpoints": create_checkpoints
        "increase_budget": increase_budget
        "opt_in_to_tiny": opt_in_to_tiny
        "init": init
    end
        # "get_tiny_power_of_at": get_tiny_power_of_at
        # "get_total_tiny_power": get_total_tiny_power
        # "get_total_tiny_power_at": get_total_tiny_power_at
        # "withdraw": withdraw

    block increase_budget:
        exit(1)
    end

    block opt_in_to_tiny:
        transfer(app_global_get(TINY_ASSET_ID_KEY), 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        exit(1)
    end

    block init:
        assert(!app_global_get(TOTAL_POWER_COUNT_KEY))
       
        bytes total_power = construct_total_power(0, Global.LatestTimestamp, bzero(16), bzero(16))
        save_total_power(total_power)
        exit(1)
    end

    block create_checkpoints:
        create_checkpoints(0, bzero(16))
        exit(1)
    end

    block create_lock:
        # test: Call create_lock multiple times
        # TODO: improve withdraw & create lock, box del

        # inputs
        bytes user_address = Txn.Sender
        int amount
        int lock_end_time

        # validations
        assert(Txn.GroupIndex)
        int input_txn_index = Txn.GroupIndex - 1

        assert(Gtxn[input_txn_index].Sender == user_address)
        assert(Gtxn[input_txn_index].TypeEnum == Axfer)
        assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
        assert(Gtxn[input_txn_index].XferAsset == app_global_get(TINY_ASSET_ID_KEY))
        
        amount = Gtxn[input_txn_index].AssetAmount
        assert(amount >= MIN_LOCK_AMOUNT)
        app_global_put(TOTAL_LOCKED_AMOUNT_KEY, app_global_get(TOTAL_LOCKED_AMOUNT_KEY) + amount)

        lock_end_time = btoi(Txn.ApplicationArgs[1])
        assert(!(lock_end_time % WEEK))
        assert(lock_end_time >= (Global.LatestTimestamp + WEEK))
        assert(lock_end_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))

        # create AccountState
        box<AccountState> account_state = CreateBox(user_address)
        account_state.locked_amount = amount
        account_state.lock_end_time = lock_end_time
        account_state.index = 0

        bytes slope = get_slope(amount)
        int bias = get_bias(slope, (lock_end_time - Global.LatestTimestamp))

        # create AccountPower
        bytes account_power = construct_account_power(bias, Global.LatestTimestamp, slope, Global.ZeroAddress)
        save_account_power(user_address, 0, account_power)

        create_checkpoints(bias, slope)
        update_or_create_slope_change(lock_end_time, slope)
        exit(1)
    end

    block increase_lock_amount:
        # inputs
        bytes user_address = Txn.Sender
        int amount

        # validations
        assert(Txn.GroupIndex)
        int input_txn_index = Txn.GroupIndex - 1

        assert(Gtxn[input_txn_index].Sender == user_address)
        assert(Gtxn[input_txn_index].TypeEnum == Axfer)
        assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
        assert(Gtxn[input_txn_index].XferAsset == app_global_get(TINY_ASSET_ID_KEY))
        
        amount = Gtxn[input_txn_index].AssetAmount
        assert(amount >= MIN_LOCK_INCREMENT)
        app_global_put(TOTAL_LOCKED_AMOUNT_KEY, app_global_get(TOTAL_LOCKED_AMOUNT_KEY) + amount)

        # Get AccountState
        box<AccountState> account_state = OpenBox(user_address)
        assert(account_state.lock_end_time > Global.LatestTimestamp)
        int lock_end_time = account_state.lock_end_time

        # New state values
        int old_locked_amount = account_state.locked_amount
        int new_locked_amount = old_locked_amount + amount
        int new_index = account_state.index + 1

        # Update AccountState
        account_state.locked_amount = new_locked_amount
        account_state.index = new_index

        bytes old_slope = get_slope(old_locked_amount)
        bytes new_slope = get_slope(new_locked_amount)
        bytes slope_delta = new_slope b- old_slope

        int old_bias = get_bias(old_slope, (lock_end_time - Global.LatestTimestamp))
        int new_bias = get_bias(new_slope, (lock_end_time - Global.LatestTimestamp))
        int bias_delta = new_bias - old_bias

        bytes account_power = construct_account_power(new_bias, Global.LatestTimestamp, new_slope, Global.ZeroAddress)
        save_account_power(user_address, new_index, account_power)

        create_checkpoints(bias_delta, slope_delta)
        update_or_create_slope_change(lock_end_time, slope_delta)

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
        # assert(new_lock_end_time > Global.LatestTimestamp)

        # Update AccountState
        int new_index = account_state.index + 1
        account_state.lock_end_time = new_lock_end_time
        account_state.index = new_index

        bytes slope = get_slope(account_state.locked_amount)
        int old_bias = get_bias(slope, (old_lock_end_time - Global.LatestTimestamp))
        int new_bias = get_bias(slope, (new_lock_end_time - Global.LatestTimestamp))
        int bias_delta = new_bias - old_bias

        bytes account_power = construct_account_power(new_bias, Global.LatestTimestamp, slope, Global.ZeroAddress)
        save_account_power(user_address, new_index, account_power)
        create_checkpoints(bias_delta, itob(0))
        revert_slope_change(old_lock_end_time, slope)
        update_or_create_slope_change(new_lock_end_time, slope)
        log(itob(Global.OpcodeBudget))
        exit(1)
    end

    # block get_tiny_power_of:
    #     # TODO: Handle no account
    #     bytes user_address = Txn.Accounts[1]
    #     int tiny_power = 0
    #     bytes delegatee = Global.ZeroAddress

    #     int exists
    #     exists, _ = box_len(user_address)

    #     # Get AccountState
    #     if exists:
    #         box<AccountState> account_state = OpenBox(user_address)

    #         # Get AccountPower
    #         box<AccountPower> account_power = OpenBox(concat(user_address, itob(account_state.last_index)))

    #         if (account_power.end_time > Global.LatestTimestamp) && !account_power.valid_until:
    #             int remaining_time = account_power.end_time - Global.LatestTimestamp
    #             # tiny_power = btoi((itob(account_power.locked_amount) b* itob(remaining_time)) b/ itob(MAX_LOCK_TIME))
    #             tiny_power = get_power(account_power.locked_amount, remaining_time)
    #             delegatee = account_power.delegatee
    #         end
    #     end

    #     log(itob(tiny_power))
    #     log(delegatee)
    #     exit(1)
    # end

    # block get_tiny_power_of_at:
    #     # TODO: Handle no account
    #     bytes user_address = Txn.Accounts[1]
    #     int time = btoi(Txn.ApplicationArgs[1])
    #     int index = btoi(Txn.ApplicationArgs[2])
    #     int tiny_power = 0
    #     bytes delegatee = Global.ZeroAddress
    #     int exists
    #     exists, _ = box_len(user_address)

    #     if exists:
    #         # Get AccountState
    #         box<AccountState> account_state = OpenBox(user_address)
    #         assert(index >= account_state.first_index)
    #         assert(index <= account_state.last_index)

    #         # Get AccountPower
    #         box<AccountPower> account_power = OpenBox(concat(user_address, itob(index)))
    #         assert(time >= account_power.start_time)
    #         assert(account_power.end_time >= time)
    #         assert((account_power.valid_until > time) || !account_power.valid_until)

    #         int remaining_time = account_power.end_time - time
    #         tiny_power = get_power(account_power.locked_amount, remaining_time)
    #         delegatee = account_power.delegatee
    #     end

    #     log(concat(itob(tiny_power), delegatee))
    #     exit(1)
    # end

    # block get_total_tiny_power:
    #     int time = Global.LatestTimestamp
    #     int tiny_power = get_total_power_at(time)

    #     log(itob(tiny_power))
    #     exit(1)
    # end

    # block get_total_tiny_power_at:
    #     int time = btoi(Txn.ApplicationArgs[1])
    #     int tiny_power = get_total_power_at(time)

    #     log(itob(tiny_power))
    #     exit(1)
    # end

    # block withdraw:
    #     bytes user_address = Txn.Sender

    #     # Get AccountState
    #     box<AccountState> account_state = OpenBox(user_address)
    #     assert(account_state.lock_end_time < Global.LatestTimestamp)

    #     transfer(app_global_get(TINY_ASSET_ID_KEY), account_state.locked_amount, Global.CurrentApplicationAddress, user_address)

    #     account_state.locked_amount = 0
    #     account_state.lock_end_time = 0
    #     exit(1)
    # end
end

func construct_account_power(bias: int, timestamp: int, slope: bytes, delegatee: bytes) bytes:
    return concat(concat(concat(itob(bias), itob(timestamp)), slope), delegatee)
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

func create_checkpoints(account_bias_delta: int, account_slope_delta: bytes):
    int has_account_event = account_bias_delta || (account_slope_delta != bzero(16))
    TotalPower last_total_power = get_total_power(app_global_get(TOTAL_POWER_COUNT_KEY) - 1)

    int bias = last_total_power.bias
    int last_checkpoint = last_total_power.timestamp
    bytes slope = last_total_power.slope
    bytes cumulative_power = last_total_power.cumulative_power

    int t_i = (last_checkpoint / DAY) * DAY
    bytes d_slope
    int d_bias
    int d_time

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

        d_time = t_i - last_checkpoint
        d_bias = get_bias(slope, d_time)
        if d_bias > bias:
            cumulative_power = cumulative_power b+ get_cumulative_power_2(bias, slope)
            bias = 0
        else:
            cumulative_power = cumulative_power b+ get_cumulative_power_1(bias, (bias - d_bias), d_time)
            bias = bias - d_bias
        end

        if d_slope b>= slope:
            slope = bzero(16)
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
            save_total_power(construct_total_power(bias, last_checkpoint, slope, cumulative_power))
        end
    end
    assert(!(has_account_event && (t_i != Global.LatestTimestamp)))
    return
end

func get_cumulative_power_1(old_bias: int, new_bias: int, d_time: int) bytes:
    # (old_bias + new_bias) * d_time / 2
    return ((itob(old_bias) b+ itob(new_bias)) b* itob(d_time)) b/ itob(2)
end

func get_cumulative_power_2(old_bias: int, slope: bytes) bytes:
    # d_time = old_bias / slope
    # (old_bias + new_bias) * (old_bias / slope) / 2, new_bias = 0
    # (old_bias * old_bias) / (slope * 2)
    return (itob(old_bias) b* itob(old_bias)) b/ (slope b* itob(2))
end

func get_bias(slope: bytes, time: int) int:
    return btoi((slope b* itob(time)) b/ TWO_TO_THE_64)
end

func get_total_power_at(time: int) int:
    int start_time = get_start_time_of_day(time)
    bytes box_name = concat(TOTAL_POWERS, itob(start_time))
    int exists
    exists, _ = box_len(box_name)
    assert(exists)

    box<TotalPower> total_power = OpenBox(box_name)
    int time_delta = time - start_time
    int power_delta = get_power_delta(total_power.slope, time_delta)
    int tiny_power = total_power.bias - power_delta
    return tiny_power
end

func get_power(locked_amount: int, remaining_time: int) int:
    # TODO: Is this correct?
    return btoi((get_slope(locked_amount) b* itob(remaining_time)) b/ TWO_TO_THE_64)
end

func get_power_delta(slope: bytes, time: int) int:
    return btoi((slope b* itob(time)) b/ TWO_TO_THE_64)
end

func get_start_time_of_day(time: int) int:
    return ((time / DAY) * DAY)
end

func get_start_time_of_next_day(time: int) int:
    return (((time / DAY) * DAY) + DAY)
end

func get_start_time_of_week(time: int) int:
    return ((time / WEEK) * WEEK)
end

func get_start_time_of_next_week(time: int) int:
    return (((time / WEEK) * WEEK) + WEEK)
end

func get_slope(locked_amount: int) bytes:
    return bzero(16) b| ((itob(locked_amount) b* TWO_TO_THE_64) b/ itob(MAX_LOCK_TIME))
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
