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
    execution_hash: bytes[128]
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
    app_global_put(AMM_APP_ID_KEY, btoi(Txn.ApplicationArgs[0]))
    app_global_put(PROPOSAL_VOTING_APP_ID_KEY, btoi(Txn.ApplicationArgs[1]))

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
        "set_fee_setter": set_fee_setter
        "set_fee_manager": set_fee_manager
        "set_fee_collector": set_fee_collector
        "set_fee_for_pool": set_fee_for_pool
    end

    block set_fee_setter:
        bytes proposal_id = Txn.ApplicationArgs[1]

        Proposal proposal = check_and_get_proposal(proposal_id)

        # Assert proposal.execution_hash
        # hash(method_name, address)

        bytes execution_hash = Lpad(sha256(Concat("set_fee_setter", Txn.Accounts[0])), 128)
        assert(proposal.execution_hash == execution_hash)

        inner_txn:
            TypeEnum: Appl
            ApplicationID: app_global_get(AMM_APP_ID_KEY)
            ApplicationArgs[0]: "set_fee_setter"
            Accounts[0]: Txn.Accounts[0]
            Fee: 0
        end
        exit(1)
    end

    block set_fee_manager:
        bytes proposal_id = Txn.ApplicationArgs[1]

        Proposal proposal = check_and_get_proposal(proposal_id)

        # Assert proposal.execution_hash
        # hash(method_name, address)
        bytes execution_hash = Lpad(sha256(Concat("set_fee_manager", Txn.Accounts[0])), 128)
        assert(proposal.execution_hash == execution_hash)

        inner_txn:
            TypeEnum: Appl
            ApplicationID: app_global_get(AMM_APP_ID_KEY)
            ApplicationArgs[0]: "set_fee_manager"
            Accounts[0]: Txn.Accounts[0]
            Fee: 0
        end
        exit(1)
    end

    block set_fee_collector:
        bytes proposal_id = Txn.ApplicationArgs[1]

        Proposal proposal = check_and_get_proposal(proposal_id)

        # Assert proposal.execution_hash
        # hash(method_name, address)
        bytes execution_hash = Lpad(sha256(Concat("set_fee_collector", Txn.Accounts[0])), 128)
        assert(proposal.execution_hash == execution_hash)

        inner_txn:
            TypeEnum: Appl
            ApplicationID: app_global_get(AMM_APP_ID_KEY)
            ApplicationArgs[0]: "set_fee_manager"
            Accounts[0]: Txn.Accounts[0]
            Fee: 0
        end
        exit(1)
    end

    block set_fee_for_pool:
        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes total_fee_share = Txn.ApplicationArgs[2]
        bytes protocol_fee_ratio = Txn.ApplicationArgs[3]

        Proposal proposal = check_and_get_proposal(proposal_id)

        # Assert proposal.execution_hash
        # hash(method_name, pool_address: address, total_fee_share, protocol_fee_ratio)
        
        bytes execution_hash = Lpad(sha256(Concat(Concat(Concat("set_fee_for_pool", Txn.Accounts[1]), total_fee_share), protocol_fee_ratio)), 128)
        assert(proposal.execution_hash == execution_hash)

        inner_txn:
            TypeEnum: Appl
            ApplicationID: app_global_get(AMM_APP_ID_KEY)
            ApplicationArgs[0]: "set_fee"
            ApplicationArgs[1]: total_fee_share
            ApplicationArgs[2]: protocol_fee_ratio
            Accounts[0]: Txn.Accounts[1]
            Fee: 0
        end
        exit(1)
    end
end

func check_and_get_proposal(proposal_id: bytes) Proposal:

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
    assert(proposal_state == PROPOSAL_STATE_SUCCEEDED)

    return proposal
end