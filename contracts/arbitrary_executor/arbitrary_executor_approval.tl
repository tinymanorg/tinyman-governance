#pragma version 9
#tealish version git+https://github.com/Hipo/tealish.git@c4f8c12b36dbcc8b63844df5f818164925735087

# Taken from Proposal Voting App
# Size: 123 bytes
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
const bytes VALIDATE_TRANSACTION_HASH_PREFIX = "vt"
const bytes VALIDATE_GROUP_HASH_PREFIX = "vg"

router:
    create_application
    update_application
    validate_transaction
    validate_group
end

@public(OnCompletion=CreateApplication)
func create_application(proposal_voting_app_id: int):
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
func validate_transaction(proposal_id: bytes):
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

    # proposal checks
    assert(proposal_state == PROPOSAL_STATE_SUCCEEDED)

    # Assert Gtxn[1]'s transaction_id.
    bytes execution_hash = Concat(VALIDATE_TRANSACTION_HASH_PREFIX, Gtxn[1].TxID)
    assert(execution_hash == proposal.execution_hash)

    set_proposal_as_executed(proposal_id)
    return
end

@public()
func validate_group(proposal_id: bytes):
    # 1. Read proposal from proposal_voting app
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(PROPOSAL_VOTING_APP_ID_KEY)
        ApplicationArgs[0]: "get_proposal"
        ApplicationArgs[1]: proposal_id
        Fee: 0
    end

    Proposal proposal = Cast(extract(4, 182, Itxn.LastLog), Proposal)
    int proposal_state = btoi(extract(186, 0, Itxn.LastLog))

    # 2 .Proposal checks
    assert(proposal_state == PROPOSAL_STATE_SUCCEEDED)

    bytes execution_hash = Concat(VALIDATE_GROUP_HASH_PREFIX, Global.GroupID)
    assert(execution_hash == proposal.execution_hash)

    set_proposal_as_executed(proposal_id)

    return
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