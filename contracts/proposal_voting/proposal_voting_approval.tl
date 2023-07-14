#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

# Name: <proposal_id>
# Size: 120 bytes
struct Proposal:
    index: int
    creation_timestamp: int
    voting_start_timestamp: int
    voting_end_timestamp: int
    snapshot_total_voting_power: int
    vote_count: int
    quorum_numerator: int
    against_vote_amount: int
    for_vote_amount: int
    abstain_vote_amount: int
    is_cancelled: bytes[1]
    is_executed: bytes[1]
    is_quorum_reached: bytes[1]
    proposer: bytes[32]
end

# 24 * 60 * 60
const int DAY = 86400
const bytes BIT_ZERO = "\x00"
const bytes BIT_ONE = "\x01"

# Global States
const bytes LOCKING_APP_ID_KEY = "locking_app_id"
const bytes PROPOSAL_ID_COUNTER_KEY = "proposal_id_counter"
const bytes VOTING_DELAY_KEY = "voting_delay"
const bytes VOTING_DURATION_KEY = "voting_duration"
const bytes PROPOSAL_THRESHOLD_KEY = "proposal_threshold"
const bytes QUORUM_NUMERATOR_KEY = "quorum_numerator"
const bytes MANAGER_KEY = "manager"
const bytes PROPOSAL_MANAGER_KEY = "proposal_manager"

# Box
const bytes PROPOSAL_BOX_PREFIX = "p"
const bytes ATTENDANCE_BOX_PREFIX = "a"
const int ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE = 24

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
    app_global_put(LOCKING_APP_ID_KEY, Txn.ApplicationArgs[0])
    app_global_put(PROPOSAL_ID_COUNTER_KEY, 0)

    # voting starts at the N'th midnight after creation time
    app_global_put(VOTING_DELAY_KEY, 2)

    # voting ends at the N'th midnight after voting start time
    app_global_put(VOTING_DURATION_KEY, 7)

    # proposer must hold %N of the total voting power
    app_global_put(PROPOSAL_THRESHOLD_KEY, 5)

    # %N of the total voting power must be voted for accept or abstain
    app_global_put(QUORUM_NUMERATOR_KEY, 50)

    app_global_put(MANAGER_KEY, Txn.Sender)
    app_global_put(PROPOSAL_MANAGER_KEY, Txn.Sender)
    exit(1)
end

block update_app:
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))
    exit(1)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "cast_vote": cast_vote
        "create_proposal": create_proposal
        "increase_budget": increase_budget
        "get_proposal": get_proposal
        "has_voted": has_voted
        "cancel_proposal": cancel_proposal
        "execute_proposal": execute_proposal
        "set_manager": set_manager
        "set_proposal_manager": set_proposal_manager
    end

    block increase_budget:
        # Box read budget and opcode budget will be incread using this method.
        exit(1)
    end

    block set_manager:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        app_global_put(MANAGER_KEY, Txn.ApplicationArgs[1])
        exit(1)
    end

    block set_proposal_manager:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        app_global_put(PROPOSAL_MANAGER_KEY, Txn.ApplicationArgs[1])
        exit(1)
    end

    block cancel_proposal:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes proposal_box_name = concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)
        assert(proposal.is_cancelled == BIT_ZERO)
        assert(proposal.is_executed == BIT_ZERO)
        proposal.is_cancelled = BIT_ONE

        # TODO: Add event log
        exit(1)
    end

    block execute_proposal:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes proposal_box_name = concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)
        assert(proposal.is_cancelled == BIT_ZERO)
        assert(proposal.is_executed == BIT_ZERO)
        proposal.is_executed = BIT_ONE

        # TODO: Add event log
        exit(1)
    end

    block create_proposal:
        bytes user_address = Txn.Sender
        bytes proposal_id = Txn.ApplicationArgs[1]
        int exists

        # 1. Check voting power threshold
        int account_voting_power = get_account_voting_power(user_address)
        int total_voting_power = get_total_voting_power()
        assert((itob(account_voting_power) b* itob(100)) b>= (itob(total_voting_power) b* itob(app_global_get(PROPOSAL_THRESHOLD_KEY))))

        # 2. Check proposal id
        # proposal id will be calculate off-chian. it is 32 bit hash of the proposal data.
        assert(len(proposal_id) ==  32)
        bytes proposal_box_name = concat(PROPOSAL_BOX_PREFIX, proposal_id)
        exists, _ = box_len(proposal_box_name)
        assert(!exists)

        # 3. Start & End time
        int voting_start_timestamp = ((Global.LatestTimestamp / DAY) * DAY) + (app_global_get(VOTING_DELAY_KEY) * DAY)
        int voting_end_timestamp = voting_start_timestamp + (app_global_get(VOTING_DURATION_KEY) * DAY)

        # 4. Save box
        box<Proposal> proposal = CreateBox(proposal_box_name)
        proposal.index = app_global_get(PROPOSAL_ID_COUNTER_KEY)
        proposal.creation_timestamp = Global.LatestTimestamp
        proposal.voting_start_timestamp = voting_start_timestamp
        proposal.voting_end_timestamp = voting_end_timestamp
        proposal.snapshot_total_voting_power = total_voting_power
        proposal.proposer = user_address
        proposal.quorum_numerator = app_global_get(QUORUM_NUMERATOR_KEY)

        app_global_put(PROPOSAL_ID_COUNTER_KEY, app_global_get(PROPOSAL_ID_COUNTER_KEY) + 1)

        log(itob(Global.OpcodeBudget))

        # TODO: Add event log.
        exit(1)
    end

    block cast_vote:
        bytes user_address = Txn.Sender
        bytes proposal_id = Txn.ApplicationArgs[1]
        int vote = btoi(Txn.ApplicationArgs[2])
        bytes account_power_index = Txn.ApplicationArgs[3]

        # Get proposal
        bytes proposal_box_name = concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)

        # Proposal Active
        # proposal.voting_start_timestamp <= Global.LatestTimestamp < proposal.voting_end_timestamp
        assert(proposal.voting_start_timestamp <= Global.LatestTimestamp)
        assert(Global.LatestTimestamp < proposal.voting_end_timestamp)
        assert(proposal.is_cancelled == BIT_ZERO)

        # Check attendance
        int proposal_index = proposal.index
        set_user_as_voted(user_address, proposal_index)

        # Get voting power
        int account_voting_power = get_account_voting_power_at(user_address, Global.LatestTimestamp, account_power_index)

        # Count Vote
        if vote == 0:
            proposal.against_vote_amount = proposal.against_vote_amount + account_voting_power
        elif vote == 1:
            proposal.for_vote_amount = proposal.for_vote_amount + account_voting_power
        elif vote == 2:
            proposal.abstain_vote_amount = proposal.abstain_vote_amount + account_voting_power
        else:
            error()
        end
        
        if (itob(proposal.snapshot_total_voting_power) b* itob(proposal.quorum_numerator)) b<= (itob(proposal.for_vote_amount + proposal.abstain_vote_amount) b* itob(100)):
            proposal.is_quorum_reached = BIT_ONE
        end

        # Update proposal
        proposal.vote_count = proposal.vote_count + 1

        # TODO: Add event log.
        log(itob(Global.OpcodeBudget))
        exit(1)
    end

    block get_proposal:
        bytes proposal_id = Txn.ApplicationArgs[1]

        bytes proposal_box_name = concat(PROPOSAL_BOX_PREFIX, proposal_id)
        int exists
        bytes proposal
        exists, proposal = box_get(proposal_box_name)

        log(proposal)
        exit(1)
    end

    block has_voted:
        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes address = Txn.ApplicationArgs[2]

        bytes proposal_box_name = concat(PROPOSAL_BOX_PREFIX, proposal_id)
        int exists
        bytes proposal_data
        exists, proposal_data = box_get(proposal_box_name)
        Proposal proposal = proposal_data

        log(itob(has_voted(address, proposal.index)))
        exit(1)
    end
end

func has_voted(address: bytes, proposal_index: int) int:
    int box_index = proposal_index / ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE
    int array_index = proposal_index % ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE
    bytes box_name = concat(ATTENDANCE_BOX_PREFIX, concat(address, itob(box_index)))

    int exists
    exists, _ = box_len(box_name)
    if exists:
        return box_extract(box_name, array_index, 1) == BIT_ONE
    end
    return 0
end

func set_user_as_voted(address: bytes, proposal_index: int):
    int box_index = proposal_index / ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE
    int array_index = proposal_index % ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE
    bytes box_name = concat(ATTENDANCE_BOX_PREFIX, concat(address, itob(box_index)))

    _ = box_create(box_name, ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE)
    assert(box_extract(box_name, array_index, 1) == BIT_ZERO)
    box_replace(box_name, array_index, BIT_ONE)
    return
end

# Inner App Calls
func get_account_voting_power(address: bytes) int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_tiny_power_of"
        ApplicationArgs[1]: address
        Fee: 0
    end
    int voting_power = extract_uint64(Itxn.LastLog, 0)
    return voting_power
end

func get_account_voting_power_at(address: bytes, snapshot_timestamp: int, account_power_index: bytes) int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_tiny_power_of_at"
        ApplicationArgs[1]: address
        ApplicationArgs[2]: itob(snapshot_timestamp)
        ApplicationArgs[3]: account_power_index
        Fee: 0
    end
    int voting_power = extract_uint64(Itxn.LastLog, 0)
    return voting_power
end

func get_total_voting_power() int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_total_tiny_power"
        Fee: 0
    end
    int voting_power = extract_uint64(Itxn.LastLog, 0)
    return voting_power
end
