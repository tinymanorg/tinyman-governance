#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@e8d1b27620220bc4e520d7d3b6d62523e13a7723

# Name: <proposal_id>
# Size: 116 bytes
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

const int PROPOSAL_STATE_WAITING_FOR_APPROVAL = 0
const int PROPOSAL_STATE_CANCELLED = 1
const int PROPOSAL_STATE_PENDING = 2
const int PROPOSAL_STATE_ACTIVE = 3
const int PROPOSAL_STATE_DEFEATED = 4
const int PROPOSAL_STATE_SUCCEEDED = 5
const int PROPOSAL_STATE_EXECUTED = 6

const int VOTE_AGAINST = 0
const int VOTE_FOR = 1
const int VOTE_ABSTAIN = 2

# Global States
const bytes VAULT_APP_ID_KEY = "vault_app_id"
const bytes PROPOSAL_INDEX_COUNTER_KEY = "proposal_index_counter"
const bytes VOTING_DELAY_KEY = "voting_delay"
const bytes VOTING_DURATION_KEY = "voting_duration"
const bytes PROPOSAL_THRESHOLD_KEY = "proposal_threshold"
const bytes PROPOSAL_THRESHOLD_NUMERATOR_KEY = "proposal_threshold_numerator"
const bytes QUORUM_THRESHOLD_KEY = "quorum_threshold"
const bytes MANAGER_KEY = "manager"
const bytes PROPOSAL_MANAGER_KEY = "proposal_manager"
const bytes APPROVAL_REQUIREMENT_KEY = "approval_requirement"

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
    app_global_put(VAULT_APP_ID_KEY, btoi(Txn.ApplicationArgs[0]))
    app_global_put(PROPOSAL_INDEX_COUNTER_KEY, 0)

    # voting starts at the N'th midnight after creation time
    app_global_put(VOTING_DELAY_KEY, 2)

    # voting ends at the N'th midnight after voting start time
    app_global_put(VOTING_DURATION_KEY, 7)

    # proposer must have X amount total voting power, default 450K
    app_global_put(PROPOSAL_THRESHOLD_KEY, 450000000000)

    # proposer must have %N of the total voting power, default %0
    app_global_put(PROPOSAL_THRESHOLD_NUMERATOR_KEY, 0)

    # required voting power (for or abstain) to reach quorum, default 2,25M (2_250_000_000_000)
    app_global_put(QUORUM_THRESHOLD_KEY, 2250000000000)

    app_global_put(APPROVAL_REQUIREMENT_KEY, 1)

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
        "approve_proposal": approve_proposal
        "cancel_proposal": cancel_proposal
        "execute_proposal": execute_proposal
        "get_box": get_box
        "get_proposal_state": get_proposal_state
        "set_manager": set_manager
        "set_proposal_manager": set_proposal_manager
        "set_voting_delay": set_voting_delay
        "set_voting_duration": set_voting_duration
        "set_proposal_threshold": set_proposal_threshold
        "set_proposal_threshold_numerator": set_proposal_threshold_numerator
        "set_quorum_threshold": set_quorum_threshold
        "disable_approval_requirement": disable_approval_requirement
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

    block set_manager:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        bytes new_manager = Txn.ApplicationArgs[1]
        assert(len(new_manager) == 32)
        app_global_put(MANAGER_KEY, new_manager)
        log(Concat(method("set_manager(address)"), new_manager))
        exit(1)
    end

    block set_proposal_manager:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(MANAGER_KEY))

        bytes set_proposal_manager = Txn.ApplicationArgs[1]
        assert(len(set_proposal_manager) == 32)
        app_global_put(PROPOSAL_MANAGER_KEY, set_proposal_manager)
        log(Concat(method("set_proposal_manager(address)"), set_proposal_manager))
        exit(1)
    end

    block approve_proposal:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)
        assert(proposal.is_approved == BYTES_FALSE)
        assert(!proposal.voting_start_timestamp)
        assert(proposal.is_cancelled == BYTES_FALSE)

        int voting_start_timestamp = get_voting_start_timestamp()
        int voting_end_timestamp = voting_start_timestamp + (app_global_get(VOTING_DURATION_KEY) * DAY)

        proposal.is_approved = BYTES_TRUE
        proposal.voting_start_timestamp = voting_start_timestamp
        proposal.voting_end_timestamp = voting_end_timestamp

        bytes proposal_data
        _, proposal_data = box_get(proposal_box_name)
        log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address)"), proposal_id, proposal_data))
        log(Concat(method("approve_proposal(address,byte[59])"), user_address, proposal_id))
        exit(1)
    end

    block cancel_proposal:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)
        assert(proposal.is_cancelled == BYTES_FALSE)
        assert(proposal.is_executed == BYTES_FALSE)
        assert((Global.LatestTimestamp < proposal.voting_end_timestamp) || !proposal.voting_end_timestamp)
        proposal.is_cancelled = BYTES_TRUE

        bytes proposal_data
        _, proposal_data = box_get(proposal_box_name)
        log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address)"), proposal_id, proposal_data))
        log(Concat(method("cancel_proposal(address,byte[59])"), user_address, proposal_id))
        exit(1)
    end

    block execute_proposal:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)
        assert(proposal.voting_end_timestamp)
        assert(proposal.voting_end_timestamp < Global.LatestTimestamp)
        assert(proposal.is_cancelled == BYTES_FALSE)
        assert(proposal.is_executed == BYTES_FALSE)
        assert(proposal.is_quorum_reached == BYTES_TRUE)
        assert(proposal.for_voting_power > proposal.against_voting_power)
        proposal.is_executed = BYTES_TRUE

        bytes proposal_data
        _, proposal_data = box_get(proposal_box_name)
        log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address)"), proposal_id, proposal_data))
        log(Concat(method("execute_proposal(address,byte[59])"), user_address, proposal_id))
        exit(1)
    end

    block create_proposal:
        bytes user_address = Txn.Sender
        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes execution_hash = Txn.ApplicationArgs[2]

        # 1. Check voting power threshold
        int account_voting_power = get_account_voting_power(user_address)
        int total_voting_power = get_total_voting_power()

        # Tiny power amount threshold
        assert(account_voting_power >= app_global_get(PROPOSAL_THRESHOLD_KEY))
        # Tiny power percentage threshold
        assert((itob(account_voting_power) b* itob(100)) b>= (itob(total_voting_power) b* itob(app_global_get(PROPOSAL_THRESHOLD_NUMERATOR_KEY))))

        # 2. Check proposal id
        # proposal id will be calculated off-chain. it is base32 string representation of CID (base32 - cidv1 - raw - (sha2-256 : 256 : <metadata>))
        assert(len(proposal_id) == 59)
        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)

        # 3. Create Box
        box<Proposal> proposal = CreateBox(proposal_box_name)
        proposal.index = app_global_get(PROPOSAL_INDEX_COUNTER_KEY)
        proposal.creation_timestamp = Global.LatestTimestamp
        proposal.snapshot_total_voting_power = total_voting_power
        proposal.proposer = Cast(user_address, bytes[32])
        proposal.quorum_threshold = app_global_get(QUORUM_THRESHOLD_KEY)
        proposal.execution_hash = Rpad(execution_hash, 128)

        # 4. Start & End time
        if !app_global_get(APPROVAL_REQUIREMENT_KEY):
            int voting_start_timestamp = get_voting_start_timestamp()
            int voting_end_timestamp = voting_start_timestamp + (app_global_get(VOTING_DURATION_KEY) * DAY)
            proposal.voting_start_timestamp = voting_start_timestamp
            proposal.voting_end_timestamp = voting_end_timestamp
        end

        app_global_put(PROPOSAL_INDEX_COUNTER_KEY, app_global_get(PROPOSAL_INDEX_COUNTER_KEY) + 1)

        bytes proposal_data
        _, proposal_data = box_get(proposal_box_name)
        log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address)"), proposal_id, proposal_data))
        log(Concat(method("create_proposal(address,byte[59])"), user_address, proposal_id))
        exit(1)
    end

    block cast_vote:
        bytes user_address = Txn.Sender
        bytes proposal_id = Txn.ApplicationArgs[1]
        int vote = btoi(Txn.ApplicationArgs[2])
        bytes account_power_index = Txn.ApplicationArgs[3]

        # Get proposal
        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)

        # Proposal Active
        assert(proposal.is_cancelled == BYTES_FALSE)

        # proposal.voting_start_timestamp <= Global.LatestTimestamp < proposal.voting_end_timestamp
        assert(proposal.voting_start_timestamp)
        assert(proposal.voting_start_timestamp <= Global.LatestTimestamp)
        assert(Global.LatestTimestamp < proposal.voting_end_timestamp)

        # Check attendance
        check_and_set_user_as_voted(user_address, proposal.index)

        # Get voting power
        int account_voting_power = get_account_voting_power_at(user_address, proposal.creation_timestamp, account_power_index)
        assert(account_voting_power)

        # Count Vote
        if vote == VOTE_AGAINST:
            proposal.against_voting_power = proposal.against_voting_power + account_voting_power
        elif vote == VOTE_FOR:
            proposal.for_voting_power = proposal.for_voting_power + account_voting_power
        elif vote == VOTE_ABSTAIN:
            proposal.abstain_voting_power = proposal.abstain_voting_power + account_voting_power
        else:
            Error()
        end
        
        if proposal.quorum_threshold <= (proposal.for_voting_power + proposal.abstain_voting_power):
            proposal.is_quorum_reached = BYTES_TRUE
        end

        # Update proposal
        proposal.vote_count = proposal.vote_count + 1

        bytes proposal_data
        _, proposal_data = box_get(proposal_box_name)
        log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address)"), proposal_id, proposal_data))
        log(Concat(method("cast_vote(address,byte[59],uint64,uint64)"), user_address, proposal_id, itob(vote), itob(account_voting_power)))
        exit(1)
    end

    block set_voting_delay:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        int new_voting_delay = btoi(Txn.ApplicationArgs[1])
        app_global_put(VOTING_DELAY_KEY, new_voting_delay)
        log(Concat(method("set_voting_delay(uint64)"), itob(new_voting_delay)))
        exit(1)
    end

    block set_voting_duration:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        int new_voting_duration = btoi(Txn.ApplicationArgs[1])
        assert(new_voting_duration)
        app_global_put(VOTING_DURATION_KEY, new_voting_duration)
        log(Concat(method("set_voting_duration(uint64)"), itob(new_voting_duration)))
        exit(1)
    end

    block set_proposal_threshold:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        int new_proposal_threshold = btoi(Txn.ApplicationArgs[1])
        app_global_put(PROPOSAL_THRESHOLD_KEY, new_proposal_threshold)
        log(Concat(method("set_proposal_threshold(uint64)"), itob(new_proposal_threshold)))
        exit(1)
    end

    block set_proposal_threshold_numerator:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        int new_proposal_threshold_numerator = btoi(Txn.ApplicationArgs[1])
        assert(new_proposal_threshold_numerator <= 100)
        app_global_put(PROPOSAL_THRESHOLD_NUMERATOR_KEY, new_proposal_threshold_numerator)
        log(Concat(method("set_proposal_threshold_numerator(uint64)"), itob(new_proposal_threshold_numerator)))
        exit(1)
    end

    block set_quorum_threshold:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        int new_quorum_threshold = btoi(Txn.ApplicationArgs[1])
        app_global_put(QUORUM_THRESHOLD_KEY, new_quorum_threshold)
        log(Concat(method("set_quorum_threshold(uint64)"), itob(new_quorum_threshold)))
        exit(1)
    end

    block disable_approval_requirement:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        assert(app_global_get(APPROVAL_REQUIREMENT_KEY))
        app_global_put(APPROVAL_REQUIREMENT_KEY, 0)
        log(method("disable_approval_requirement()"))
        exit(1)
    end

    block get_proposal:
        bytes proposal_id = Txn.ApplicationArgs[1]

        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
        int exists
        bytes proposal_data
        exists, proposal_data = box_get(proposal_box_name)
        assert(exists)

        log(Concat(method("return"), proposal_data))
        exit(1)
    end

    block has_voted:
        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes address = Txn.ApplicationArgs[2]

        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)

        bytes result
        if has_voted(address, proposal.index):
            result = BYTES_TRUE
        else:
            result = BYTES_FALSE
        end

        log(Concat(method("return"), result))
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

    block get_proposal_state:
        bytes proposal_id = Txn.ApplicationArgs[1]

        bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)

        int state
        if proposal.is_cancelled == BYTES_TRUE:
            state = PROPOSAL_STATE_CANCELLED
        elif proposal.is_executed == BYTES_TRUE:
            state = PROPOSAL_STATE_EXECUTED
        elif !proposal.voting_start_timestamp:
            state = PROPOSAL_STATE_WAITING_FOR_APPROVAL
        elif Global.LatestTimestamp < proposal.voting_start_timestamp:
            state = PROPOSAL_STATE_PENDING
        elif Global.LatestTimestamp < proposal.voting_end_timestamp:
            state = PROPOSAL_STATE_ACTIVE
        elif (proposal.is_quorum_reached == BYTES_FALSE) || (proposal.for_voting_power <= proposal.against_voting_power):
            state = PROPOSAL_STATE_DEFEATED
        else:
            state = PROPOSAL_STATE_SUCCEEDED
        end

        log(Concat(method("return"), itob(state)))
        exit(1)
    end
end

func has_voted(address: bytes, proposal_index: int) int:
    int box_index = proposal_index / (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    int array_index = proposal_index % (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    bytes box_name = Concat(ATTENDANCE_BOX_PREFIX, Concat(address, itob(box_index)))

    int exists
    exists, _ = box_len(box_name)
    if exists:
        return getbit(box_extract(box_name, 0, ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE), array_index)
    end
    return 0
end

func check_and_set_user_as_voted(address: bytes, proposal_index: int):
    int box_index = proposal_index / (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    int array_index = proposal_index % (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    bytes box_name = Concat(ATTENDANCE_BOX_PREFIX, Concat(address, itob(box_index)))

    bytes sheet
    _ = box_create(box_name, ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE)
    _, sheet = box_get(box_name)
    assert(!getbit(sheet, array_index))
    sheet = setbit(sheet, array_index, 1)
    box_put(box_name, sheet)
    return
end

func get_voting_start_timestamp() int:
    int end_timestamp_of_day = ((Global.LatestTimestamp / DAY) * DAY) + DAY
    return end_timestamp_of_day + (app_global_get(VOTING_DELAY_KEY) * DAY)
end

# Inner App Calls
func increase_budget():
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(VAULT_APP_ID_KEY)
        ApplicationArgs[0]: "increase_budget"
        Fee: 0
    end
    return
end

func get_account_voting_power(address: bytes) int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(VAULT_APP_ID_KEY)
        ApplicationArgs[0]: "get_tiny_power_of"
        ApplicationArgs[1]: address
        Fee: 0
    end
    int voting_power = extract_uint64(Itxn.LastLog, 4)
    return voting_power
end

func get_account_voting_power_at(address: bytes, snapshot_timestamp: int, account_power_index: bytes) int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(VAULT_APP_ID_KEY)
        ApplicationArgs[0]: "get_tiny_power_of_at"
        ApplicationArgs[1]: address
        ApplicationArgs[2]: itob(snapshot_timestamp)
        ApplicationArgs[3]: account_power_index
        Fee: 0
    end
    int voting_power = extract_uint64(Itxn.LastLog, 4)
    return voting_power
end

func get_total_voting_power() int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(VAULT_APP_ID_KEY)
        ApplicationArgs[0]: "get_total_tiny_power"
        Fee: 0
    end
    int voting_power = extract_uint64(Itxn.LastLog, 4)
    return voting_power
end
