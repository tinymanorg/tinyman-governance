#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@e8d1b27620220bc4e520d7d3b6d62523e13a7723

# Name: rp<box_index><array_index>
# Size: 24 bytes
struct RewardPeriod:
    total_reward_amount: int
    total_cumulative_power_delta: bytes[16]
end

# Name: rh<box_index><array_index>
# Size: 16 bytes
struct RewardHistory:
    timestamp: int
    reward_amount: int
end

const int WEEK = 604800

# Global states
const bytes TINY_ASSET_ID_KEY = "tiny_asset_id"
const bytes VAULT_APP_ID_KEY = "vault_app_id"
const bytes FIRST_PERIOD_TIMESTAMP = "first_period_timestamp"
const bytes REWARD_PERIOD_COUNT_KEY = "reward_period_count"
const bytes REWARD_HISTORY_COUNT_KEY = "reward_history_count"
const bytes MANAGER_KEY = "manager"
const bytes REWARDS_MANAGER_KEY = "rewards_manager"

# Boxes
const bytes REWARD_CLAIM_SHEET_BOX_PREFIX = "c"
# Boolean (1012 * 8 bits)
const int REWARD_CLAIM_SHEET_BOX_SIZE = 1012

const bytes REWARD_PERIOD_BOX_PREFIX = "rp"
const int REWARD_PERIOD_SIZE = 24
const int REWARD_PERIOD_BOX_SIZE = 1008
const int REWARD_PERIOD_BOX_ARRAY_LEN = 42

const bytes REWARD_HISTORY_BOX_PREFIX = "rh"
const int REWARD_HISTORY_SIZE = 16
const int REWARD_HISTORY_BOX_SIZE = 256
const int REWARD_HISTORY_BOX_ARRAY_LEN = 16


if !Txn.ApplicationID:
    jump create_app
end

switch Txn.OnCompletion:
    NoOp: main
    OptIn: fail
    CloseOut: fail
    UpdateApplication: update_app
    DeleteApplication: fail
end

block fail:
    exit(0)
end

block create_app:
    app_global_put(TINY_ASSET_ID_KEY, btoi(Txn.ApplicationArgs[0]))
    app_global_put(VAULT_APP_ID_KEY, btoi(Txn.ApplicationArgs[1]))
    app_global_put(REWARD_PERIOD_COUNT_KEY, 0)
    app_global_put(REWARD_HISTORY_COUNT_KEY, 0)
    app_global_put(FIRST_PERIOD_TIMESTAMP, 0)
    app_global_put(MANAGER_KEY, Txn.Sender)
    app_global_put(REWARDS_MANAGER_KEY, Txn.Sender)
    exit(1)
end

block update_app:
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))
    exit(1)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "claim_rewards": claim_rewards
        "increase_budget": increase_budget
        "create_reward_period": create_reward_period
        "set_reward_amount": set_reward_amount
        "get_box": get_box
        "set_manager": set_manager
        "set_rewards_manager": set_rewards_manager
        "init": init
    end

    block increase_budget:
        # Box read budget and opcode budget will be incread using this method.
        if Txn.NumAppArgs > 1:
            int inner_app_call_count = btoi(Txn.ApplicationArgs[1])
            for _ in 0:inner_app_call_count:
                increase_budget()
            end
        end
        exit(1)
    end

    block create_reward_period:
        bytes total_power_index_1 = Txn.ApplicationArgs[1]
        bytes total_power_index_2 = Txn.ApplicationArgs[2]
        int reward_amount_index = btoi(Txn.ApplicationArgs[3])

        int period_index = app_global_get(REWARD_PERIOD_COUNT_KEY)
        int period_start_timestamp = get_period_start_timestamp(period_index)
        int period_end_timestamp = period_start_timestamp + WEEK
        assert(period_end_timestamp <= Global.LatestTimestamp)

        bytes cumulative_power_delta = get_total_cumulative_power_delta(period_start_timestamp, period_end_timestamp, total_power_index_1, total_power_index_2)
        int total_reward_amount = get_reward_amount(period_start_timestamp, reward_amount_index)

        bytes reward_period = construct_reward_period(total_reward_amount, cumulative_power_delta)
        save_reward_period(reward_period)
        log(Concat(method("create_reward_period(uint64,uint64,uint128)"), itob(period_index), itob(total_reward_amount), cumulative_power_delta))
        exit(1)
    end

    block claim_rewards:
        bytes user_address = Txn.Sender

        int period_index_start = btoi(Txn.ApplicationArgs[1])
        int period_count = btoi(Txn.ApplicationArgs[2])
        bytes account_power_indexes = Txn.ApplicationArgs[3]
        assert(period_count)

        int timestamp = get_period_start_timestamp(period_index_start)
        int total_reward_amount = 0
        bytes reward_amounts = ""

        int period_index
        int period_start_timestamp
        int period_end_timestamp
        bytes account_power_index_1
        bytes account_power_index_2
        int reward_amount
        bytes account_cumulative_power_delta

        for i in 0:period_count:
            period_index = period_index_start + i
            check_and_save_claimed_reward(user_address, period_index)

            period_start_timestamp = timestamp + (i * WEEK)
            period_end_timestamp = period_start_timestamp + WEEK

            account_power_index_1 = extract3(account_power_indexes, (i * 8), 8)
            account_power_index_2 = extract3(account_power_indexes, ((i + 1) * 8), 8)
            
            account_cumulative_power_delta = get_account_cumulative_power_delta(user_address, period_start_timestamp, period_end_timestamp, account_power_index_1, account_power_index_2)

            RewardPeriod reward_period = get_reward_period(period_index)
            reward_amount = btoi((itob(reward_period.total_reward_amount) b* account_cumulative_power_delta) b/ reward_period.total_cumulative_power_delta)

            reward_amounts = Concat(reward_amounts, itob(reward_amount))
            total_reward_amount = total_reward_amount + reward_amount
        end

        transfer(app_global_get(TINY_ASSET_ID_KEY), total_reward_amount, Global.CurrentApplicationAddress, user_address)
        
        # claim_rewards(address, total_reward_amount, period_index_start, period_count, reward_amounts[])
        log(Concat(method("claim_rewards(address,uint64,uint64,uint64,uint64[])"), user_address, itob(total_reward_amount), itob(period_index_start), itob(period_count), extract(6, 2, itob(period_count)), reward_amounts))
        exit(1)
    end

    block init:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(REWARDS_MANAGER_KEY))
        assert(!app_global_get(REWARD_HISTORY_COUNT_KEY))

        int first_period_timestamp = ((Global.LatestTimestamp / WEEK) * WEEK) + WEEK
        app_global_put(FIRST_PERIOD_TIMESTAMP, first_period_timestamp)

        int reward_amount = btoi(Txn.ApplicationArgs[1])
        bytes reward_history = construct_reward_history(first_period_timestamp, reward_amount)
        save_reward_history(reward_history)

        # opt-in to TINY
        transfer(app_global_get(TINY_ASSET_ID_KEY), 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        log(Concat(method("init(uint64,uint64)"), itob(first_period_timestamp), itob(reward_amount)))
        exit(1)
    end
    
    block set_reward_amount:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(REWARDS_MANAGER_KEY))

        # Ensure that the app is initialized.
        assert(app_global_get(REWARD_HISTORY_COUNT_KEY))

        int reward_amount = btoi(Txn.ApplicationArgs[1])
        bytes reward_history = construct_reward_history(Global.LatestTimestamp, reward_amount)
        save_reward_history(reward_history)
        log(Concat(method("set_reward_amount(uint64,uint64)"), itob(Global.LatestTimestamp), itob(reward_amount)))
        exit(1)
    end

    block set_manager:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        bytes new_manager = Txn.ApplicationArgs[1]
        assert(len(new_manager) == 32)
        app_global_put(MANAGER_KEY, new_manager)
        log(Concat(method("set_manager(address)"), new_manager))
        exit(1)
    end
    
    block set_rewards_manager:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        bytes new_rewards_manager = Txn.ApplicationArgs[1]
        assert(len(new_rewards_manager) == 32)
        app_global_put(REWARDS_MANAGER_KEY, new_rewards_manager)
        log(Concat(method("set_rewards_manager(address)"), new_rewards_manager))
        exit(1)
    end

    block get_box:
        bytes box_name = Txn.ApplicationArgs[1]

        int exists
        bytes data
        exists, data = box_get(box_name)

        log(Concat(method("return"), itob(exists), extract(6, 2, itob(len(data))), data))
        exit(1)
    end
end

func construct_reward_period(amount: int, cumulative_power_delta: bytes) bytes:
    return Concat(itob(amount), (bzero(16) b| cumulative_power_delta))
end

func save_reward_period(data: bytes):
    int index = app_global_get(REWARD_PERIOD_COUNT_KEY)

    assert(len(data) == REWARD_PERIOD_SIZE)
    int box_index = index / REWARD_PERIOD_BOX_ARRAY_LEN
    int array_index = index % REWARD_PERIOD_BOX_ARRAY_LEN
    bytes box_name = Concat(REWARD_PERIOD_BOX_PREFIX, itob(box_index))
    _ = box_create(box_name, REWARD_PERIOD_BOX_SIZE)
    box_replace(box_name, (array_index * REWARD_PERIOD_SIZE), data)

    # reward_period(index, total_reward_amount, total_cumulative_power_delta)
    log(Concat(method("reward_period(uint64,uint64,uint128)"), itob(index), data))
    app_global_put(REWARD_PERIOD_COUNT_KEY, index + 1)
    return
end

func get_reward_period(index: int) RewardPeriod:
    int box_index = index / REWARD_PERIOD_BOX_ARRAY_LEN
    int array_index = index % REWARD_PERIOD_BOX_ARRAY_LEN
    bytes box_name = Concat(REWARD_PERIOD_BOX_PREFIX, itob(box_index))
    return Cast(box_extract(box_name, (array_index * REWARD_PERIOD_SIZE), REWARD_PERIOD_SIZE), RewardPeriod)
end

func construct_reward_history(timestamp: int, reward_amount: int) bytes:
    return Concat(itob(timestamp), itob(reward_amount))
end

func save_reward_history(data: bytes):
    int index = app_global_get(REWARD_HISTORY_COUNT_KEY)

    assert(len(data) == REWARD_HISTORY_SIZE)
    int box_index = index / REWARD_HISTORY_BOX_ARRAY_LEN
    int array_index = index % REWARD_HISTORY_BOX_ARRAY_LEN
    bytes box_name = Concat(REWARD_HISTORY_BOX_PREFIX, itob(box_index))
    _ = box_create(box_name, REWARD_HISTORY_BOX_SIZE)
    box_replace(box_name, (array_index * REWARD_HISTORY_SIZE), data)

    # reward_history(index, timestamp, reward_amount)
    log(Concat(method("reward_history(uint64,uint64,uint64)"), itob(index), data))
    app_global_put(REWARD_HISTORY_COUNT_KEY, index + 1)
    return
end

func get_reward_history(index: int) RewardHistory:
    int box_index = index / REWARD_HISTORY_BOX_ARRAY_LEN
    int array_index = index % REWARD_HISTORY_BOX_ARRAY_LEN
    bytes box_name = Concat(REWARD_HISTORY_BOX_PREFIX, itob(box_index))
    return Cast(box_extract(box_name, (array_index * REWARD_HISTORY_SIZE), REWARD_HISTORY_SIZE), RewardHistory)
end

func get_reward_amount(timestamp: int, reward_history_index: int) int:
    assert(reward_history_index < app_global_get(REWARD_HISTORY_COUNT_KEY))
    assert(timestamp <= Global.LatestTimestamp)

    RewardHistory reward_history = get_reward_history(reward_history_index)
    assert(reward_history.timestamp <= timestamp)

    if (reward_history_index + 1) < app_global_get(REWARD_HISTORY_COUNT_KEY):
        RewardHistory next_reward_history = get_reward_history(reward_history_index + 1)
        assert(timestamp < next_reward_history.timestamp)
    end
    return reward_history.reward_amount
end

func check_and_save_claimed_reward(address: bytes, period_index: int):
    int box_index = period_index / (REWARD_CLAIM_SHEET_BOX_SIZE * 8)
    int array_index = period_index % (REWARD_CLAIM_SHEET_BOX_SIZE * 8)
    bytes box_name = Concat(REWARD_CLAIM_SHEET_BOX_PREFIX, address, itob(box_index))

    bytes sheet
    _ = box_create(box_name, REWARD_CLAIM_SHEET_BOX_SIZE)
    _, sheet = box_get(box_name)
    assert(!getbit(sheet, array_index))
    sheet = setbit(sheet, array_index, 1)
    box_put(box_name, sheet)
    return
end

func get_period_start_timestamp(period_index: int) int:
    return app_global_get(FIRST_PERIOD_TIMESTAMP) + (period_index * WEEK)
end

# Inner Txns
func get_account_cumulative_power_delta(address: bytes, timestamp_1: int, timestamp_2: int, account_power_index_1: bytes, account_power_index_2: bytes) bytes:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(VAULT_APP_ID_KEY)
        ApplicationArgs[0]: "get_account_cumulative_power_delta"
        ApplicationArgs[1]: address
        ApplicationArgs[2]: itob(timestamp_1)
        ApplicationArgs[3]: itob(timestamp_2)
        ApplicationArgs[4]: account_power_index_1
        ApplicationArgs[5]: account_power_index_2
        Fee: 0
    end

    bytes cumulative_power_delta = extract(4, 16, Itxn.LastLog)
    return cumulative_power_delta
end

func get_total_cumulative_power_delta(timestamp_1: int, timestamp_2: int, total_power_index_1: bytes, total_power_index_2: bytes) bytes:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(VAULT_APP_ID_KEY)
        ApplicationArgs[0]: "get_total_cumulative_power_delta"
        ApplicationArgs[1]: itob(timestamp_1)
        ApplicationArgs[2]: itob(timestamp_2)
        ApplicationArgs[3]: total_power_index_1
        ApplicationArgs[4]: total_power_index_2
        Fee: 0
    end

    bytes cumulative_power_delta = extract(4, 16, Itxn.LastLog)
    return cumulative_power_delta
end

func increase_budget():
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(VAULT_APP_ID_KEY)
        ApplicationArgs[0]: "increase_budget"
        Fee: 0
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
