#pragma version 9
#tealish version git+https://github.com/Hipo/tealish.git@c4f8c12b36dbcc8b63844df5f818164925735087

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
    execution_hash: bytes[34]
    executor: bytes[32]
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


router:
    create_application
    update_application
    cast_vote
    create_proposal
    increase_budget
    get_proposal
    has_voted
    approve_proposal
    cancel_proposal
    execute_proposal
    get_box
    set_manager
    set_proposal_manager
    set_voting_delay
    set_voting_duration
    set_proposal_threshold
    set_proposal_threshold_numerator
    set_quorum_threshold
    disable_approval_requirement
end


@public(OnCompletion=CreateApplication)
func create_application(vault_app_id: int):
    app_global_put(VAULT_APP_ID_KEY, vault_app_id)
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
    return
end

@public(OnCompletion=UpdateApplication)
func update_application():
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))
    return
end

@public()
func increase_budget(inner_app_call_count: int):
    # Box read budget and opcode budget will be incread using this method.
    for _ in 0:inner_app_call_count:
        inner_txn:
            TypeEnum: Appl
            ApplicationID: app_global_get(VAULT_APP_ID_KEY)
            ApplicationArgs[0]: "increase_budget"
            Fee: 0
        end
    end
    return
end

@public()
func set_manager(new_manager: bytes):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))

    assert(len(new_manager) == 32)
    app_global_put(MANAGER_KEY, new_manager)
    log(Concat(method("set_manager(address)"), new_manager))
    return
end

@public()
func set_proposal_manager(new_proposal_manager: bytes):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))

    assert(len(new_proposal_manager) == 32)
    app_global_put(PROPOSAL_MANAGER_KEY, new_proposal_manager)
    log(Concat(method("set_proposal_manager(address)"), new_proposal_manager))
    return
end

@public()
func approve_proposal(proposal_id: bytes):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

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
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address,byte[34],address)"), proposal_id, proposal_data))
    log(Concat(method("approve_proposal(address,byte[59])"), user_address, proposal_id))
    return
end

@public()
func cancel_proposal(proposal_id: bytes):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
    box<Proposal> proposal = OpenBox(proposal_box_name)
    assert(proposal.is_cancelled == BYTES_FALSE)
    assert(proposal.is_executed == BYTES_FALSE)
    assert((Global.LatestTimestamp < proposal.voting_end_timestamp) || !proposal.voting_end_timestamp)
    proposal.is_cancelled = BYTES_TRUE

    bytes proposal_data
    _, proposal_data = box_get(proposal_box_name)
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address,byte[34],address)"), proposal_id, proposal_data))
    log(Concat(method("cancel_proposal(address,byte[59])"), user_address, proposal_id))
    return
end

@public()
func execute_proposal(proposal_id: bytes):
    bytes user_address = Txn.Sender

    bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
    box<Proposal> proposal = OpenBox(proposal_box_name)
    assert(proposal.voting_end_timestamp)
    assert(proposal.voting_end_timestamp < Global.LatestTimestamp)
    assert(proposal.is_cancelled == BYTES_FALSE)
    assert(proposal.is_executed == BYTES_FALSE)
    assert(proposal.is_quorum_reached == BYTES_TRUE)
    assert(proposal.for_voting_power > proposal.against_voting_power)
    proposal.is_executed = BYTES_TRUE

    assert(user_address == proposal.executor)

    bytes proposal_data
    _, proposal_data = box_get(proposal_box_name)
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address,byte[34],address)"), proposal_id, proposal_data))
    log(Concat(method("execute_proposal(address,byte[59])"), user_address, proposal_id))
    return
end

@public()
func create_proposal(proposal_id: bytes):
    bytes user_address = Txn.Sender

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
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address,byte[34],address)"), proposal_id, proposal_data))
    log(Concat(method("create_proposal(address,byte[59])"), user_address, proposal_id))
    return
end

@public()
func cast_vote(proposal_id: bytes, vote: int, account_power_index: bytes):
    bytes user_address = Txn.Sender

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
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bool,bool,bool,bool,address,byte[34],address)"), proposal_id, proposal_data))
    log(Concat(method("cast_vote(address,byte[59],uint64,uint64)"), user_address, proposal_id, itob(vote), itob(account_voting_power)))
    return
end

@public()
func set_voting_delay(new_voting_delay: int):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    app_global_put(VOTING_DELAY_KEY, new_voting_delay)
    log(Concat(method("set_voting_delay(uint64)"), itob(new_voting_delay)))
    return
end

@public()
func set_voting_duration(new_voting_duration: int):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    assert(new_voting_duration)
    app_global_put(VOTING_DURATION_KEY, new_voting_duration)
    log(Concat(method("set_voting_duration(uint64)"), itob(new_voting_duration)))
    return
end

@public()
func set_proposal_threshold(new_proposal_threshold: int):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    app_global_put(PROPOSAL_THRESHOLD_KEY, new_proposal_threshold)
    log(Concat(method("set_proposal_threshold(uint64)"), itob(new_proposal_threshold)))
    return
end

@public()
func set_proposal_threshold_numerator(new_proposal_threshold_numerator: int):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    assert(new_proposal_threshold_numerator <= 100)
    app_global_put(PROPOSAL_THRESHOLD_NUMERATOR_KEY, new_proposal_threshold_numerator)
    log(Concat(method("set_proposal_threshold_numerator(uint64)"), itob(new_proposal_threshold_numerator)))
    return
end

@public()
func set_quorum_threshold(new_quorum_threshold: int):
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    app_global_put(QUORUM_THRESHOLD_KEY, new_quorum_threshold)
    log(Concat(method("set_quorum_threshold(uint64)"), itob(new_quorum_threshold)))
    return
end

@public()
func disable_approval_requirement():
    bytes user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    assert(app_global_get(APPROVAL_REQUIREMENT_KEY))
    app_global_put(APPROVAL_REQUIREMENT_KEY, 0)
    log(method("disable_approval_requirement()"))
    return
end

@public()
func get_proposal(proposal_id: bytes) bytes, int:
    bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
    int exists
    bytes proposal_data
    exists, proposal_data = box_get(proposal_box_name)
    assert(exists)

    Proposal proposal = Cast(proposal_data, Proposal)

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

    return proposal_data, state
end

@public()
func has_voted(proposal_id: bytes, address: bytes) bytes:
    bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
    box<Proposal> proposal = OpenBox(proposal_box_name)

    int proposal_index = proposal.index
    int box_index = proposal_index / (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    int array_index = proposal_index % (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    bytes box_name = Concat(ATTENDANCE_BOX_PREFIX, Concat(address, itob(box_index)))

    int exists
    exists, _ = box_len(box_name)
    bytes result = BYTES_FALSE
    if exists:
        if getbit(box_extract(box_name, 0, ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE), array_index):
            result = BYTES_TRUE
        end
    end

    return result
end

@public()
func get_box(box_name: bytes) int, bytes, bytes:
    int exists
    bytes data
    exists, data = box_get(box_name)

    return exists, extract(6, 2, itob(len(data))), data
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
