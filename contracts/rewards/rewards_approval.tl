#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

# Name: <address>
# Size: 24 bytes
struct AccountState:
    latest_cumulat: int
    lock_end_time: int
    power_count: int
end


# 2^64 - 1
const int MAX_UINT64 = 18446744073709551615
# 24 * 60 * 60
const int DAY = 86400
# 7 * DAY
const int WEEK = 604800

const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes BYTE_ZERO_128 = "\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes TINY_ASSET_ID_KEY = "tiny_asset_id"
const bytes LOCKING_APP_ID_KEY = "locking_app_id"
const bytes CREATION_TIMESTAMP_KEY = "creation_timestamp"

const bytes BIT_ZERO = "\x00"
const bytes BIT_ONE = "\x01"

const int REWARD_SHEET_SIZE = 1024


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
    int creation_timestamp
    _, creation_timestamp = app_global_get_ex(Txn.Applications[1], CREATION_TIMESTAMP_KEY)
    app_global_put(CREATION_TIMESTAMP_KEY, creation_timestamp)
    exit(1)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "claim_rewards": claim_rewards
        "init": init
    end

    block claim_rewards:
        bytes user_address = Txn.Sender

        int timestamp = btoi(Txn.ApplicationArgs[1])
        bytes account_power_index_1 = Txn.ApplicationArgs[2]
        bytes account_power_index_2 = Txn.ApplicationArgs[3]
        bytes total_power_index_1 = Txn.ApplicationArgs[4]
        bytes total_power_index_2 = Txn.ApplicationArgs[5]

        assert(!(timestamp % DAY))
        int end_timestamp = timestamp + DAY

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
        int total_reward_amount = get_total_reward_amount(timestamp)
        int reward_amount = btoi((itob(total_reward_amount) b* account_cumulative_power_delta) b/ total_cumulative_power_delta)
        transfer(app_global_get(TINY_ASSET_ID_KEY), reward_amount, Global.CurrentApplicationAddress, user_address)

        # TODO: Event Log
        log(itob(Global.OpcodeBudget))
        exit(1)
    end

    block init:
        opt_in_to_asset_if_needed(app_global_get(TINY_ASSET_ID_KEY))
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

func get_total_reward_amount(timestamp: int) int:
    # TODO: Fix dummy consts
    # 2024/01/01
    if timestamp < 1704067200:
        return 100000000
    else:
        # TODO: Get from global state
        return 1
    end
    return 0
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
    return (timestamp / DAY) - (app_global_get(CREATION_TIMESTAMP_KEY) / DAY)
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


func opt_in_to_asset_if_needed(asset_id: int):
    if asset_id:
        int is_opted_in
        is_opted_in, _ = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, asset_id)

        if is_opted_in == 0:
            transfer(asset_id, 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        end
    end
    return
end