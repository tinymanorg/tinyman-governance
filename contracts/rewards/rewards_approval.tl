#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

# Name: <address>
# Size: 24 bytes
struct AccountState:
    latest_cumulat: int
    lock_end_time: int
    power_count: int
end

# Name: rh<box_index><array_index>
# Size: 16 bytes
struct RewardHistory:
    timestamp: int
    reward_amount: int
end


# 2^64 - 1
# const int MAX_UINT64 = 18446744073709551615
# 7 * DAY
const int WEEK = 604800

const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes BYTE_ZERO_128 = "\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes TINY_ASSET_ID_KEY = "tiny_asset_id"
const bytes LOCKING_APP_ID_KEY = "locking_app_id"
const bytes CREATION_TIMESTAMP_KEY = "creation_timestamp"
const bytes REWARD_HISTORY_COUNT_KEY = "reward_history_count"
const bytes MANAGER_KEY = "manager"
const bytes REWARD_HISTORY = "rh"

const bytes BIT_ZERO = "\x00"
const bytes BIT_ONE = "\x01"

const int REWARD_SHEET_SIZE = 1024

const int REWARD_HISTORY_SIZE = 16
const int REWARD_HISTORY_BOX_SIZE = 1024
const int REWARD_HISTORY_BOX_ARRAY_LEN = 16

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
    app_global_put(LOCKING_APP_ID_KEY, Txn.Applications[1])
    app_global_put(REWARD_HISTORY_COUNT_KEY, 0)

    int creation_timestamp
    _, creation_timestamp = app_global_get_ex(Txn.Applications[1], CREATION_TIMESTAMP_KEY)
    app_global_put(CREATION_TIMESTAMP_KEY, creation_timestamp)

    app_global_put(MANAGER_KEY, Txn.Sender)
    exit(1)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "claim_rewards": claim_rewards
        "init": init
        "set_reward_amount": set_reward_amount
        "set_manager": set_manager
    end

    block claim_rewards:
        bytes user_address = Txn.Sender

        int timestamp = btoi(Txn.ApplicationArgs[1])
        bytes account_power_index_1 = Txn.ApplicationArgs[2]
        bytes account_power_index_2 = Txn.ApplicationArgs[3]
        bytes total_power_index_1 = Txn.ApplicationArgs[4]
        bytes total_power_index_2 = Txn.ApplicationArgs[5]
        int reward_amount_index = btoi(Txn.ApplicationArgs[6])

        assert(!(timestamp % WEEK))
        int end_timestamp = timestamp + WEEK

        # TODO: Make sure the reward is not send previously
        int period_index = get_period_index(timestamp)
        check_and_save_claimed_reward(user_address, period_index)

        bytes account_cumulative_1
        bytes account_cumulative_2
        bytes total_cumulative_power_1
        bytes total_cumulative_power_2

        # Make 1 inner app call instead of 4
        # account_cumulative_1 = get_account_cumulative_power_at(user_address, timestamp, account_power_index_1)
        # account_cumulative_2 = get_account_cumulative_power_at(user_address, end_timestamp, account_power_index_2)
        # total_cumulative_power_1 = get_total_cumulative_power_at(timestamp, total_power_index_1)
        # total_cumulative_power_2 = get_total_cumulative_power_at(end_timestamp, total_power_index_2)
        # OR
        account_cumulative_1, account_cumulative_2, total_cumulative_power_1, total_cumulative_power_2 = get_cumulative_powers_for_rewards(user_address, timestamp, end_timestamp, account_power_index_1, account_power_index_2, total_power_index_1, total_power_index_2)
        # log(account_cumulative_1)
        # log(account_cumulative_2)
        # log(total_cumulative_power_1)
        # log(total_cumulative_power_2)

        bytes account_cumulative_power_delta = account_cumulative_2 b- account_cumulative_1
        bytes total_cumulative_power_delta = total_cumulative_power_2 b- total_cumulative_power_1

        # TODO: Calculate real reward amount
        int total_reward_amount = get_reward_amount(timestamp, reward_amount_index)
        int reward_amount = btoi((itob(total_reward_amount) b* account_cumulative_power_delta) b/ total_cumulative_power_delta)

        # TODO: Event Log
        log(itob(Global.OpcodeBudget))

        transfer(app_global_get(TINY_ASSET_ID_KEY), reward_amount, Global.CurrentApplicationAddress, user_address)
        exit(1)
    end

    block init:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))
        assert(!app_global_get(REWARD_HISTORY_COUNT_KEY))

        int reward_amount = btoi(Txn.ApplicationArgs[1])
        bytes reward_history = construct_reward_history(app_global_get(CREATION_TIMESTAMP_KEY), reward_amount)
        save_reward_history(reward_history)

        # opt-in to TINY
        transfer(app_global_get(TINY_ASSET_ID_KEY), 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        exit(1)
    end
    
    block set_reward_amount:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        # Ensure that the app is initialized.
        assert(app_global_get(REWARD_HISTORY_COUNT_KEY))

        int reward_amount = btoi(Txn.ApplicationArgs[1])
        bytes reward_history = construct_reward_history(Global.LatestTimestamp, reward_amount)
        save_reward_history(reward_history)

        exit(1)
    end

    block set_manager:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        app_global_put(MANAGER_KEY, Txn.Accounts[1])
        exit(1)
    end
end

func get_account_cumulative_power_at(address: bytes, snapshot_time: int, account_power_index: bytes) bytes:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_cumulative_power_of_at"
        ApplicationArgs[1]: itob(snapshot_time)
        ApplicationArgs[2]: account_power_index
        Accounts[0]: address
        Fee: 0
    end

    bytes cumulative_power = Itxn.LastLog
    return cumulative_power
end

func get_total_cumulative_power_at(snapshot_time: int, total_power_index: bytes) bytes:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_total_cumulative_power_at"
        ApplicationArgs[1]: itob(snapshot_time)
        ApplicationArgs[2]: total_power_index
        Fee: 0
    end

    bytes cumulative_power = Itxn.LastLog
    return cumulative_power
end

func get_cumulative_powers_for_rewards(address: bytes, timestamp_1: int, timestamp_2: int, account_power_index_1: bytes, account_power_index_2: bytes, total_power_index_1: bytes, total_power_index_2: bytes) bytes, bytes, bytes, bytes:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_cumulative_powers_for_rewards"
        ApplicationArgs[1]: itob(timestamp_1)
        ApplicationArgs[2]: itob(timestamp_2)
        ApplicationArgs[3]: account_power_index_1
        ApplicationArgs[4]: account_power_index_2
        ApplicationArgs[5]: total_power_index_1
        ApplicationArgs[6]: total_power_index_2
        Accounts[0]: address
        Fee: 0
    end

    return extract(0, 16, Itxn.LastLog), extract(16, 16, Itxn.LastLog), extract(32, 16, Itxn.LastLog), extract(48, 16, Itxn.LastLog)
end

func get_reward_amount(timestamp: int, index: int) int:
    assert(index < app_global_get(REWARD_HISTORY_COUNT_KEY))
    assert(timestamp <= Global.LatestTimestamp)

    RewardHistory reward_history = get_reward_history(index)
    assert(reward_history.timestamp <= timestamp)

    if (index + 1) < app_global_get(REWARD_HISTORY_COUNT_KEY):
        TotalPower next_reward_history = get_reward_history(index + 1)
        assert(timestamp < next_reward_history.timestamp)
    end
    return reward_history.reward_amount
end

func check_and_save_claimed_reward(address: bytes, period_index: int):
    int box_index = period_index / REWARD_SHEET_SIZE
    int array_index = period_index % REWARD_SHEET_SIZE
    bytes box_name = concat(address, itob(box_index))
    _ = box_create(box_name, REWARD_SHEET_SIZE)
    assert(box_extract(box_name, array_index, 1) == BIT_ZERO)
    box_replace(box_name, array_index, BIT_ONE)
    return
end

func get_period_index(timestamp: int) int:
    return (timestamp / WEEK) - (app_global_get(CREATION_TIMESTAMP_KEY) / WEEK)
end

func construct_reward_history(timestamp: int, reward_amount: int) bytes:
    return concat(itob(timestamp), itob(reward_amount))
end

func save_reward_history(data: bytes):
    int index = app_global_get(REWARD_HISTORY_COUNT_KEY)

    assert(len(data) == REWARD_HISTORY_SIZE)
    int box_index = index / REWARD_HISTORY_BOX_ARRAY_LEN
    int array_index = index % REWARD_HISTORY_BOX_ARRAY_LEN
    bytes box_name = concat(REWARD_HISTORY, itob(box_index))
    _ = box_create(box_name, REWARD_HISTORY_BOX_SIZE)
    box_replace(box_name, (array_index * REWARD_HISTORY_SIZE), data)

    app_global_put(REWARD_HISTORY_COUNT_KEY, index + 1)
    return
end

func get_reward_history(index: int) bytes:
    int box_index = index / REWARD_HISTORY_BOX_ARRAY_LEN
    int array_index = index % REWARD_HISTORY_BOX_ARRAY_LEN
    bytes box_name = concat(REWARD_HISTORY, itob(box_index))
    return box_extract(box_name, (array_index * REWARD_HISTORY_SIZE), REWARD_HISTORY_SIZE)
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
