#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@e8d1b27620220bc4e520d7d3b6d62523e13a7723

# Taken from Proposal Voting App
struct Proposal:
    index: int
    creation_timestamp: int
    voting_start_timestamp: int
    voting_end_timestamp: int
    snapshot_total_voting_power: int
    vote_count: int
    quorum_threshold: int
    against_voting_power: int
    for_voting_power: int
    abstain_voting_power: int
    is_approved: bytes[1]
    is_cancelled: bytes[1]
    is_executed: bytes[1]
    is_quorum_reached: bytes[1]
    proposer: bytes[32]
    execution_hash: bytes[34]
    executor: bytes[32]
end

# 24 * 60 * 60
const int DAY = 86400
const bytes BYTES_FALSE = "\x00"
const bytes BYTES_TRUE = "\x80"

const bytes MANAGER_KEY = "manager"
const bytes AMM_APP_ID_KEY = "amm_app_id"
const bytes PROPOSAL_VOTING_APP_ID_KEY = "proposal_voting_app_id"

# Proposal States
const int PROPOSAL_STATE_WAITING_FOR_APPROVAL = 0
const int PROPOSAL_STATE_CANCELLED = 1
const int PROPOSAL_STATE_PENDING = 2
const int PROPOSAL_STATE_ACTIVE = 3
const int PROPOSAL_STATE_DEFEATED = 4
const int PROPOSAL_STATE_SUCCEEDED = 5
const int PROPOSAL_STATE_EXECUTED = 6

# Execution Hash Prefixes

const bytes SET_FEE_SETTER_HASH_PREFIX = "fs"
const bytes SET_FEE_MANAGER_HASH_PREFIX = "fm"
const bytes SET_FEE_COLLECTOR_HASH_PREFIX = "fc"
const bytes SET_FEE_FOR_POOL_HASH_PREFIX = "sf"

router:
    create_application
    update_application
    set_fee_setter
    set_fee_manager
    set_fee_collector
    set_fee_for_pool
    asset_optin
end

@public(OnCompletion=CreateApplication)
func create_application(amm_app_id: int, proposal_voting_app_id: int):
    app_global_put(AMM_APP_ID_KEY, amm_app_id)
    app_global_put(PROPOSAL_VOTING_APP_ID_KEY, proposal_voting_app_id)

    app_global_put(MANAGER_KEY, Txn.Sender)

    return
end

@public(OnCompletion=UpdateApplication)
func update_application():
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))

    return
end

@public()
func set_fee_setter(proposal_id: bytes, new_fee_setter: bytes[32]):
    Proposal proposal = check_and_get_proposal(proposal_id)

    # Assert proposal.execution_hash
    bytes execution_hash = Concat(SET_FEE_SETTER_HASH_PREFIX, sha256(Concat("set_fee_setter", new_fee_setter)))
    assert(proposal.execution_hash == execution_hash)

    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(AMM_APP_ID_KEY)
        ApplicationArgs[0]: "set_fee_setter"
        Accounts[0]: new_fee_setter
        Fee: 0
    end

    set_proposal_as_executed(proposal_id)
    return
end

@public()
func set_fee_manager(proposal_id: bytes, new_fee_manager: bytes[32]):
    Proposal proposal = check_and_get_proposal(proposal_id)

    # Assert proposal.execution_hash
    bytes execution_hash = Concat(SET_FEE_MANAGER_HASH_PREFIX, sha256(Concat("set_fee_manager", new_fee_manager)))
    assert(proposal.execution_hash == execution_hash)

    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(AMM_APP_ID_KEY)
        ApplicationArgs[0]: "set_fee_manager"
        Accounts[0]: new_fee_manager
        Fee: 0
    end

    set_proposal_as_executed(proposal_id)
    return
end

@public()
func set_fee_collector(proposal_id: bytes, new_fee_collector: bytes[32]):
    Proposal proposal = check_and_get_proposal(proposal_id)

    # Assert proposal.execution_hash
    bytes execution_hash = Concat(SET_FEE_COLLECTOR_HASH_PREFIX, sha256(Concat("set_fee_collector", new_fee_collector)))
    assert(proposal.execution_hash == execution_hash)

    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(AMM_APP_ID_KEY)
        ApplicationArgs[0]: "set_fee_manager"
        Accounts[0]: new_fee_collector
        Fee: 0
    end

    set_proposal_as_executed(proposal_id)
    return
end

@public()
func set_fee_for_pool(proposal_id: bytes, pool_address: bytes[32], total_fee_share: bytes, protocol_fee_ratio: bytes):
    Proposal proposal = check_and_get_proposal(proposal_id)

    # Assert proposal.execution_hash
    # prefix + hash(method_name, pool_address: address, total_fee_share, protocol_fee_ratio)
    bytes execution_hash = Concat(SET_FEE_FOR_POOL_HASH_PREFIX, sha256(Concat("set_fee_for_pool", pool_address, total_fee_share, protocol_fee_ratio)))
    assert(proposal.execution_hash == execution_hash)

    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(AMM_APP_ID_KEY)
        ApplicationArgs[0]: "set_fee"
        ApplicationArgs[1]: total_fee_share
        ApplicationArgs[2]: protocol_fee_ratio
        Accounts[0]: pool_address
        Fee: 0
    end

    set_proposal_as_executed(proposal_id)
    return
end

@public()
func asset_optin(asset_id: int):
    int index = Txn.GroupIndex
    assert(index != 0)

    assert(Gtxn[index - 1].TypeEnum == Pay)
    assert(Gtxn[index - 1].Sender == Txn.Sender)
    assert(Gtxn[index - 1].Receiver == Global.CurrentApplicationAddress)
    assert(Gtxn[index - 1].Amount == 100000)

    transfer(asset_id, 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)

    return
end

func check_and_get_proposal(proposal_id: bytes) Proposal:
    # Read proposal from proposal_voting app
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(PROPOSAL_VOTING_APP_ID_KEY)
        ApplicationArgs[0]: "get_proposal"
        ApplicationArgs[1]: proposal_id
        Fee: 0
    end

    Proposal proposal = Cast(extract(4, 182, Itxn.LastLog), Proposal)
    int proposal_state = btoi(extract(186, 0, Itxn.LastLog))

    # Proposal checks
    assert(proposal_state == PROPOSAL_STATE_SUCCEEDED)

    return proposal
end

func set_proposal_as_executed(proposal_id: bytes):
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(PROPOSAL_VOTING_APP_ID_KEY)
        ApplicationArgs[0]: "execute_proposal"
        ApplicationArgs[1]: proposal_id
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
