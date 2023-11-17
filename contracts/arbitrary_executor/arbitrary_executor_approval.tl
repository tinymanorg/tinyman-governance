#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@e8d1b27620220bc4e520d7d3b6d62523e13a7723

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
    execution_hash: bytes[128]
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
    app_global_put(PROPOSAL_VOTING_APP_ID_KEY, btoi(Txn.ApplicationArgs[0]))

    app_global_put(MANAGER_KEY, Txn.Sender)
    exit(1)
end

block update_app:
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))
    exit(1)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "validate_transaction": validate_transaction
    end

    block validate_transaction:

        bytes proposal_id = Txn.ApplicationArgs[1]

        # read proposal from proposal_voting app
        inner_txn:
            TypeEnum: Appl
            ApplicationID: app_global_get(PROPOSAL_VOTING_APP_ID_KEY)
            ApplicationArgs[0]: "get_proposal"
            ApplicationArgs[1]: proposal_id
            Fee: 0
        end

        Proposal proposal = Cast(extract(4, 0, Itxn.LastLog), Proposal)

        inner_txn:
            TypeEnum: Appl
            ApplicationID: app_global_get(PROPOSAL_VOTING_APP_ID_KEY)
            ApplicationArgs[0]: "get_proposal_state"
            ApplicationArgs[1]: proposal_id
            Fee: 0
        end

        int proposal_state = extract_uint64(Itxn.LastLog, 4)

        # proposal checks
        assert(proposal.is_approved == BYTES_TRUE)
        assert(proposal.is_cancelled == BYTES_FALSE)
        assert(proposal.is_executed == BYTES_FALSE)
        assert(proposal_state == PROPOSAL_STATE_SUCCEEDED)

        # assert(proposal.proposer == Txn.Sender)

        # Assert Gtxn[1]'s transaction_id.
        bytes execution_hash = Lpad(Gtxn[1].TxID, 128)
        assert(execution_hash == proposal.execution_hash)
        exit(1)
    end
end
