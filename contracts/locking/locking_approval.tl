#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

struct AccountState:
    locked_amount: int
    lock_end_time: int
    first_index: int
    last_index: int
end

struct AccountPower:
    locked_amount: int
    locked_round: int
    start_time: int
    end_time: int
    valid_until: int
    delegatee: bytes[32]
end

struct TotalPower:
    bias: int
    slope: bytes[16]
    cumulative_power: bytes[16]
end

struct SlopeChange:
    d_slope: bytes[16]
end

const int MAX_UINT64 = 18446744073709551615
const int WEEK = 604800
const int MAX_LOCK_TIME = 126144000
const int MIN_LOCK_AMOUNT = 10000000
const int MIN_LOCK_INCREMENT = 10000000
const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes BYTE_ZERO_128 = "\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes TOTAL_POWERS = "total_powers"
const bytes SLOPE_CHANGES = "slope_changes"

if Txn.ApplicationID:
    switch Txn.OnCompletion:
        NoOp: main
        OptIn: fail
        CloseOut: fail
        UpdateApplication: fail
        DeleteApplication: fail
    end
else:
    jump create_app
end

block fail:
    exit(0)
end

block create_app:
    app_global_put("tiny_asset_id", Txn.Assets[0])
    app_global_put("total_locked_amount", 0)
    app_global_put("first_index", 0)
    app_global_put("last_index", 0)
    exit(1)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "create_lock": create_lock
        "create_checkpoint": create_checkpoint
        "increase_lock_amount": increase_lock_amount
        "extend_lock_end_time": extend_lock_end_time
        "get_tiny_power_of": get_tiny_power_of
        "get_tiny_power_of_at": get_tiny_power_of_at
        "get_total_tiny_power": get_total_tiny_power
        "get_total_tiny_power_at": get_total_tiny_power_at
        "withdraw": withdraw
        "init_checkpoint": init_checkpoint
        "opt_in_to_tiny": opt_in_to_tiny
    end

    block opt_in_to_tiny:
        transfer(app_global_get("tiny_asset_id"), 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        exit(1)
    end

    block init_checkpoint:
        assert(!app_global_get("last_index"))

        int start_time = get_start_time_of_next_week(Global.LatestTimestamp)
        box<TotalPower> total_power = CreateBox(concat(TOTAL_POWERS, itob(start_time)))
        total_power.bias = 0
        total_power.slope = BYTE_ZERO_128
        total_power.cumulative_power = BYTE_ZERO_128

        app_global_put("first_index", start_time)
        app_global_put("last_index", start_time)
        exit(1)
    end

    block create_lock:
        # test: Call create_lock multiple times
        # TODO: improve withdraw & create lock, box del

        # inputs
        bytes user_address = Txn.Sender
        int amount
        int lock_start_time
        int lock_end_time

        # validations
        assert(Txn.GroupIndex)
        int input_txn_index = Txn.GroupIndex - 1

        assert(Gtxn[input_txn_index].Sender == user_address)
        assert(Gtxn[input_txn_index].TypeEnum == Axfer)
        assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
        assert(Gtxn[input_txn_index].XferAsset == app_global_get("tiny_asset_id"))
        
        amount = Gtxn[input_txn_index].AssetAmount
        assert(amount >= MIN_LOCK_AMOUNT)

        lock_start_time = btoi(Txn.ApplicationArgs[1])
        lock_end_time = btoi(Txn.ApplicationArgs[2])
        assert(!(lock_start_time % WEEK))
        assert(!(lock_end_time % WEEK))
        assert(lock_start_time == get_start_time_of_next_week(Global.LatestTimestamp))
        assert(lock_end_time >= (lock_start_time + WEEK))
        assert(lock_end_time <= (lock_start_time + MAX_LOCK_TIME))

        # create AccountState
        box<AccountState> account_state = CreateBox(user_address)
        account_state.locked_amount = amount
        account_state.lock_end_time = lock_end_time
        account_state.first_index = 0
        account_state.last_index = 0

        # create AccountPower
        box<AccountPower> account_power = CreateBox(concat(user_address, itob(0)))
        account_power.locked_amount = amount
        account_power.locked_round = Global.Round
        account_power.start_time = lock_start_time
        account_power.end_time = lock_end_time
        account_power.valid_until = 0
        account_power.delegatee = bzero(32)

        bytes slope = get_slope(amount)
        int bias = get_bias(slope, (lock_end_time - lock_start_time))

        # Update TotalPower
        assert(lock_start_time == app_global_get("last_index"))
        box<TotalPower> total_power = OpenBox(concat(TOTAL_POWERS, itob(lock_start_time)))
        total_power.bias = total_power.bias + bias
        total_power.slope = BYTE_ZERO_128 b| (total_power.slope b+ slope)

        # update or create SlopeChange
        update_or_create_slope_change(concat(SLOPE_CHANGES, itob(lock_end_time)), slope)

        app_global_put("total_locked_amount", app_global_get("total_locked_amount") + amount)
        exit(1)
    end

    block create_checkpoint:
        # TODO: Consider while loop

        int global_last_index = app_global_get("last_index")
        assert(global_last_index <= Global.LatestTimestamp)
        int next_global_last_index = global_last_index + WEEK

        box<TotalPower> last_total_power = OpenBox(concat(TOTAL_POWERS, itob(global_last_index)))
        int bias = last_total_power.bias
        bytes slope = last_total_power.slope
        bytes cumulative_power = last_total_power.cumulative_power

        # power
        bias = bias - get_bias(slope, WEEK)
        cumulative_power = cumulative_power b+ (((itob(last_total_power.bias) b+ itob(bias)) b* itob(WEEK)) b/ itob(2))

        # slope
        bytes slope_box_name = concat(SLOPE_CHANGES, itob(next_global_last_index))
        int exists
        exists, _ = box_len(slope_box_name)
        if exists:
            box<SlopeChange> slope_change = Box(slope_box_name)
            slope = slope b- slope_change.d_slope
        end

        # create new power
        box<TotalPower> new_total_power = CreateBox(concat(TOTAL_POWERS, itob(next_global_last_index)))
        new_total_power.bias = bias
        new_total_power.slope = BYTE_ZERO_128 b| slope
        new_total_power.cumulative_power = BYTE_ZERO_128 b| cumulative_power

        app_global_put("last_index", next_global_last_index)
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
        assert(Gtxn[input_txn_index].XferAsset == app_global_get("tiny_asset_id"))
        
        amount = Gtxn[input_txn_index].AssetAmount
        assert(amount >= MIN_LOCK_INCREMENT)

        # Get AccountState
        box<AccountState> account_state = OpenBox(user_address)

        # New state values
        int old_locked_amount = account_state.locked_amount
        int new_locked_amount = old_locked_amount + amount
        int new_last_index = account_state.last_index + 1

        # Update AccountState
        account_state.locked_amount = new_locked_amount
        account_state.last_index = new_last_index

        # Update the last AccountPower
        box<AccountPower> last_account_power = OpenBox(concat(user_address, itob(new_last_index - 1)))
        # last_account_power.end_round = Global.Round
        last_account_power.valid_until = Global.LatestTimestamp

        # Create AccountPower
        int lock_start_time = get_start_time_of_next_week(Global.LatestTimestamp)
        int lock_end_time = last_account_power.end_time
        box<AccountPower> new_account_power = CreateBox(concat(user_address, itob(new_last_index)))
        new_account_power.locked_amount = new_locked_amount
        new_account_power.locked_round = Global.Round
        new_account_power.start_time = lock_start_time
        new_account_power.end_time = lock_end_time
        new_account_power.valid_until = 0
        new_account_power.delegatee = last_account_power.delegatee

        bytes old_slope = get_slope(old_locked_amount)
        bytes new_slope = get_slope(new_locked_amount)
        int old_bias = get_bias(old_slope, (lock_end_time - lock_start_time))
        int new_bias = get_bias(new_slope, (lock_end_time - lock_start_time))

        # Update TotalPower
        box<TotalPower> total_power = OpenBox(concat(TOTAL_POWERS, itob(lock_start_time)))
        total_power.bias = total_power.bias + (new_bias - old_bias)
        total_power.slope = BYTE_ZERO_128 b| (total_power.slope b+ (new_slope b- old_slope))

        # Update SlopeChange
        box<SlopeChange> slope_change = OpenBox(concat(SLOPE_CHANGES, itob(lock_end_time)))
        slope_change.d_slope = BYTE_ZERO_128 b| (slope_change.d_slope b+ (new_slope b- old_slope))

        app_global_put("total_locked_amount", app_global_get("total_locked_amount") + amount)
        exit(1)
    end

    block extend_lock_end_time:
        #inputs
        bytes user_address = Txn.Sender
        int new_lock_end_time = btoi(Txn.ApplicationArgs[1])

        # validations
        assert(!(new_lock_end_time % WEEK))
        assert(new_lock_end_time > Global.LatestTimestamp)
        assert(new_lock_end_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))

        # Get AccountState
        box<AccountState> account_state = OpenBox(user_address)
        int old_lock_end_time = account_state.lock_end_time
        assert(new_lock_end_time > old_lock_end_time)
        bytes slope = get_slope(account_state.locked_amount)

        # Update AccountState
        int new_last_index = account_state.last_index + 1
        account_state.lock_end_time = new_lock_end_time
        account_state.last_index = new_last_index

        # Update the last AccountPower
        box<AccountPower> last_account_power = OpenBox(concat(user_address, itob(new_last_index - 1)))
        # last_account_power.end_round = Global.Round
        last_account_power.valid_until = Global.LatestTimestamp

        # Create AccountPower
        int new_lock_start_time = get_start_time_of_next_week(Global.LatestTimestamp)
        box<AccountPower> new_account_power = CreateBox(concat(user_address, itob(new_last_index)))
        new_account_power.locked_amount = last_account_power.locked_amount
        new_account_power.locked_round = Global.Round
        new_account_power.start_time = new_lock_start_time
        new_account_power.end_time = new_lock_end_time
        new_account_power.valid_until = 0
        new_account_power.delegatee = last_account_power.delegatee

        int old_bias = get_bias(slope, (old_lock_end_time - new_lock_start_time))
        int new_bias = get_bias(slope, (new_lock_end_time - new_lock_start_time))

        # Update TotalPower
        box<TotalPower> total_power = OpenBox(concat(TOTAL_POWERS, itob(new_lock_start_time)))
        total_power.bias = total_power.bias + (new_bias - old_bias)

        # Update SlopeChanges
        revert_slope_change(concat(SLOPE_CHANGES, itob(old_lock_end_time)), slope)
        update_or_create_slope_change(concat(SLOPE_CHANGES, itob(new_lock_end_time)), slope)

        exit(1)
    end

    block get_tiny_power_of:
        # TODO: Handle no account
        bytes user_address = Txn.Accounts[1]
        int tiny_power = 0

        # Get AccountState
        if box_len(user_address):
            box<AccountState> account_state = OpenBox(user_address)

            # Get AccountPower
            box<AccountPower> account_power = OpenBox(concat(user_address, itob(account_state.last_index)))

            if (account_power.end_time > Global.LatestTimestamp) && !account_power.valid_until:
                int remaining_time = account_power.end_time - Global.LatestTimestamp
                # tiny_power = btoi((itob(account_power.locked_amount) b* itob(remaining_time)) b/ itob(MAX_LOCK_TIME))
                tiny_power = get_power_at(account_power.locked_amount, remaining_time)
            end
        end

        log(itob(tiny_power))
        log(account_power.delegatee)
        exit(1)
    end

    block get_tiny_power_of_at:
        # TODO: Handle no account
        bytes user_address = Txn.Accounts[1]
        int time = btoi(Txn.ApplicationArgs[1])
        int index = btoi(Txn.ApplicationArgs[2])

        # Get AccountState
        box<AccountState> account_state = OpenBox(user_address)
        assert(index >= account_state.first_index)
        assert(index <= account_state.last_index)

        # Get AccountPower
        box<AccountPower> account_power = OpenBox(concat(user_address, itob(index)))
        assert(time >= account_power.start_time)
        assert(account_power.end_time >= time)
        assert((account_power.valid_until >= time) || !account_power.valid_until)

        int remaining_time = account_power.end_time - time
        int tiny_power = get_power_at(account_power.locked_amount, remaining_time)

        log(itob(tiny_power))
        log(account_power.delegatee)
        exit(1)
    end

    block get_total_tiny_power:
        int time = Global.LatestTimestamp
        int tiny_power = get_total_power_at(time)

        log(itob(tiny_power))
        exit(1)
    end

    block get_total_tiny_power_at:
        int time = btoi(Txn.ApplicationArgs[1])
        int tiny_power = get_total_power_at(time)

        log(itob(tiny_power))
        exit(1)
    end

    block withdraw:
        bytes user_address = Txn.Sender

        # Get AccountState
        box<AccountState> account_state = OpenBox(user_address)
        assert(account_state.lock_end_time < Global.LatestTimestamp)

        transfer(app_global_get("tiny_asset_id"), account_state.locked_amount, Global.CurrentApplicationAddress, user_address)

        account_state.locked_amount = 0
        account_state.lock_end_time = 0
        exit(1)
    end
end

func get_bias(slope: bytes, time: int) int:
    return btoi((slope b* itob(time)) b/ TWO_TO_THE_64)
end

func get_total_power_at(time: int) int:
    int week_start_time = get_start_time_of_week(time)
    bytes box_name = concat(TOTAL_POWERS, itob(week_start_time))
    int exists
    exists, _ = box_len(box_name)
    assert(exists)

    box<TotalPower> total_power = OpenBox(box_name)
    int time_delta = time - week_start_time
    int power_delta = get_power_delta(total_power.slope, time_delta)
    int tiny_power = total_power.bias - power_delta
    return tiny_power
end

func get_power_at(locked_amount: int, remaining_time: int) int:
    return btoi((get_slope(locked_amount) b* itob(remaining_time)) b/ TWO_TO_THE_64)
end

func get_power_delta(slope: bytes, time: int) int:
    return btoi((slope b* itob(time)) b/ TWO_TO_THE_64)
end

func get_start_time_of_week(time: int) int:
    return ((time / WEEK) * WEEK)
end

func get_start_time_of_next_week(time: int) int:
    return (((time / WEEK) * WEEK) + WEEK)
end

func get_slope(locked_amount: int) bytes:
    return ((itob(locked_amount) b* TWO_TO_THE_64) b/ itob(MAX_LOCK_TIME))
end

func revert_slope_change(box_name: bytes, slope: bytes):
    box<SlopeChange> slope_change = OpenBox(box_name)
    slope_change.d_slope = BYTE_ZERO_128 b| (slope_change.d_slope b- slope)
    return
end

func update_or_create_slope_change(box_name: bytes, slope: bytes):
    int exists
    exists, _ = box_len(box_name)
    if exists:
        box<SlopeChange> slope_change = OpenBox(box_name)
        slope_change.d_slope = BYTE_ZERO_128 b| (slope_change.d_slope b+ slope)
    else:
        box<SlopeChange> new_slope_change = CreateBox(box_name)
        new_slope_change.d_slope = BYTE_ZERO_128 b| slope
    end
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
