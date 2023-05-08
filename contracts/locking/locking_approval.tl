#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

struct UserState:
    locked_amount: int
    lock_expiration_time: int
    first_index: int
    last_index: int
end

struct UserPower:
    locked_amount: int
    start_round: int
    start_time: int
    end_round: int
    end_time: int
    expiration_time: int
    delegatee: bytes[32]
end

const int MAX_UINT64 = 18446744073709551615
const int WEEK = 604800
const int MAX_LOCK_TIME = 126144000
const int MIN_LOCK_AMOUNT = 10000000
const int MIN_LOCK_INCREMENT = 10000000

# struct Item:
#     id: int
#     foo: int
#     name: bytes[10]
# end

# box<Item> item1 = CreateBox("a")

# item1.id = 1
# item1.foo = 111
# item1.name = "Tealish   "

# log(item1.name)

if !Txn.ApplicationID:
    # Create App
    app_global_put("tiny_asset_id", Txn.Assets[0])

    app_global_put("total_locked_amount", 0)
    app_global_put("first_index", 0)
    app_global_put("last_index", 0)

    exit(1)
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

block main:
    switch Txn.ApplicationArgs[0]:
        "create_lock": create_lock
        "increase_lock_amount": increase_lock_amount
        "extend_lock_expiration_time": extend_lock_expiration_time
        "get_tiny_power_of": get_tiny_power_of
        "get_tiny_power_of_at": get_tiny_power_of_at
        "opt_in_to_tiny": opt_in_to_tiny
    end

    block opt_in_to_tiny:
        transfer(app_global_get("tiny_asset_id"), 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        exit(1)
    end

    block create_lock:
        bytes user_address = Txn.Sender
        # Test: Call create_lock multiple times

        int amount
        int lock_expiration_time

        assert(Txn.GroupIndex)
        int input_txn_index = Txn.GroupIndex - 1

        assert(Gtxn[input_txn_index].Sender == user_address)
        assert(Gtxn[input_txn_index].TypeEnum == Axfer)
        assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
        assert(Gtxn[input_txn_index].XferAsset == app_global_get("tiny_asset_id"))
        
        amount = Gtxn[input_txn_index].AssetAmount
        assert(amount >= MIN_LOCK_AMOUNT)

        lock_expiration_time = btoi(Txn.ApplicationArgs[1])
        assert(lock_expiration_time)
        
        # round it to week
        lock_expiration_time = (lock_expiration_time / WEEK) * WEEK

        assert(lock_expiration_time > Global.LatestTimestamp)
        assert(lock_expiration_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))

        # Create UserState
        box<UserState> user_state = CreateBox(user_address)
        user_state.locked_amount = amount
        user_state.lock_expiration_time = lock_expiration_time
        user_state.first_index = 0
        user_state.last_index = 0

        # Create UserPower
        box<UserPower> user_power = CreateBox(concat(user_address, itob(0)))
        user_power.locked_amount = amount
        user_power.start_round = Global.Round
        user_power.start_time = Global.LatestTimestamp
        user_power.end_round = 0
        user_power.end_time = 0
        user_power.expiration_time = lock_expiration_time
        # user_power.delegatee = "delegatee"

        app_global_put("total_locked_amount", app_global_get("total_locked_amount") + amount)
        exit(1)
    end

    block increase_lock_amount:
        bytes user_address = Txn.Sender
        int amount

        assert(Txn.GroupIndex)
        int input_txn_index = Txn.GroupIndex - 1

        assert(Gtxn[input_txn_index].Sender == user_address)
        assert(Gtxn[input_txn_index].TypeEnum == Axfer)
        assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
        assert(Gtxn[input_txn_index].XferAsset == app_global_get("tiny_asset_id"))
        
        amount = Gtxn[input_txn_index].AssetAmount
        assert(amount >= MIN_LOCK_INCREMENT)

        # Get User State
        box<UserState> user_state = OpenBox(user_address)

        # New state values
        int new_locked_amount = user_state.locked_amount + amount
        int new_last_index = user_state.last_index + 1

        # Update UserState
        user_state.locked_amount = new_locked_amount
        user_state.last_index = new_last_index

        # Update the last UserPower
        box<UserPower> old_user_power = OpenBox(concat(user_address, itob(new_last_index - 1)))
        old_user_power.end_round = Global.Round
        old_user_power.end_time = Global.LatestTimestamp

        # Create UserPower
        box<UserPower> new_user_power = CreateBox(concat(user_address, itob(new_last_index)))
        new_user_power.locked_amount = new_locked_amount
        new_user_power.start_round = Global.Round
        new_user_power.start_time = Global.LatestTimestamp
        new_user_power.end_round = 0
        new_user_power.end_time = 0
        new_user_power.expiration_time = old_user_power.expiration_time
        # new_user_power.delegatee = "delegatee"

        app_global_put("total_locked_amount", app_global_get("total_locked_amount") + amount)
        exit(1)
    end

    block extend_lock_expiration_time:
        bytes user_address = Txn.Sender
        int new_lock_expiration_time

        new_lock_expiration_time = btoi(Txn.ApplicationArgs[1])
        assert(new_lock_expiration_time)
        
        # round it to week
        new_lock_expiration_time = (new_lock_expiration_time / WEEK) * WEEK

        assert(new_lock_expiration_time > Global.LatestTimestamp)
        assert(new_lock_expiration_time <= (Global.LatestTimestamp + MAX_LOCK_TIME))

        # Get User State
        box<UserState> user_state = OpenBox(user_address)

        # New state values
        int new_last_index = user_state.last_index + 1

        # Update UserState
        assert(new_lock_expiration_time >= (user_state.lock_expiration_time + WEEK))
        user_state.lock_expiration_time = new_lock_expiration_time
        user_state.last_index = new_last_index

        # Update the last UserPower
        box<UserPower> old_user_power = OpenBox(concat(user_address, itob(new_last_index - 1)))
        old_user_power.end_round = Global.Round
        old_user_power.end_time = Global.LatestTimestamp

        # Create UserPower
        box<UserPower> new_user_power = CreateBox(concat(user_address, itob(new_last_index)))
        new_user_power.locked_amount = old_user_power.locked_amount
        new_user_power.start_round = Global.Round
        new_user_power.start_time = Global.LatestTimestamp
        new_user_power.end_round = 0
        new_user_power.end_time = 0
        new_user_power.expiration_time = new_lock_expiration_time
        # new_user_power.delegatee = "delegatee"

        exit(1)
    end


    block get_tiny_power_of:
        # TODO: Handle no account
        bytes user_address = Txn.Accounts[1]

        # Get User State
        box<UserState> user_state = OpenBox(user_address)

        # Get User Power
        box<UserPower> user_power = OpenBox(concat(user_address, itob(user_state.last_index)))

        int tiny_power = 0
        if (user_power.expiration_time > Global.LatestTimestamp) && !user_power.end_round:
            int remaining_time = user_power.expiration_time - Global.LatestTimestamp
            tiny_power = btoi((itob(user_power.locked_amount) b* itob(remaining_time)) b/ itob(MAX_LOCK_TIME))
        end

        log(itob(tiny_power))
        log(user_power.delegatee)
        exit(1)
    end

    block get_tiny_power_of_at:
        # TODO: Handle no account
        bytes user_address = Txn.Accounts[1]
        int time = btoi(Txn.ApplicationArgs[1])
        assert(time)
        int index = btoi(Txn.ApplicationArgs[2])

        # Get User State
        box<UserState> user_state = OpenBox(user_address)
        assert(index >= user_state.first_index)
        assert(index <= user_state.last_index)

        # Get User Power
        box<UserPower> user_power = OpenBox(concat(user_address, itob(index)))
        assert(user_power.expiration_time >= time)
        assert((user_power.end_time > time) || !user_power.end_time)

        int remaining_time = user_power.expiration_time - time
        int tiny_power = btoi((itob(user_power.locked_amount) b* itob(remaining_time)) b/ itob(MAX_LOCK_TIME))

        log(itob(tiny_power))
        log(user_power.delegatee)
        exit(1)
    end

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
