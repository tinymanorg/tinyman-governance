#pragma version 9
#tealish version git+https://github.com/Hipo/tealish.git@c4f8c12b36dbcc8b63844df5f818164925735087

# Name: <proposal_id>
# Size: 49 bytes
struct Proposal:
    index: int
    creation_timestamp: int
    voting_start_timestamp: int
    voting_end_timestamp: int
    voting_power: int
    vote_count: int
    is_cancelled: bytes[1]
end

# 24 * 60 * 60
const int DAY = 86400
const int MAX_OPTION_COUNT = 16
const bytes BYTES_FALSE = "\x00"
const bytes BYTES_TRUE = "\x80"

# Global States
const bytes VAULT_APP_ID_KEY = "vault_app_id"
const bytes PROPOSAL_INDEX_COUNTER_KEY = "proposal_index_counter"
const bytes VOTING_DELAY_KEY = "voting_delay"
const bytes VOTING_DURATION_KEY = "voting_duration"
const bytes MANAGER_KEY = "manager"
const bytes PROPOSAL_MANAGER_KEY = "proposal_manager"

# Box
const bytes PROPOSAL_BOX_PREFIX = "p"
const bytes VOTE_BOX_PREFIX = "v"
const bytes ATTENDANCE_BOX_PREFIX = "a"

const int PROPOSAL_ASSET_BOX_SIZE = 8
const int ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE = 24


router:
    create_application
    update_application
    increase_budget
    cast_vote
    create_proposal
    cancel_proposal
    get_box
    set_manager
    set_proposal_manager
    set_voting_delay
    set_voting_duration
end


@public(OnCompletion=CreateApplication)
func create_application(vault_app_id: int):
    app_global_put(VAULT_APP_ID_KEY, vault_app_id)
    app_global_put(PROPOSAL_INDEX_COUNTER_KEY, 0)

    app_global_put(VOTING_DELAY_KEY, 2)
    app_global_put(VOTING_DURATION_KEY, 7)

    app_global_put(MANAGER_KEY, Txn.Sender)
    app_global_put(PROPOSAL_MANAGER_KEY, Txn.Sender)
    return
end

@public(OnCompletion=UpdateApplication)
func update_application():
    bytes[32] user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))
    return
end

@public()
func create_proposal(proposal_id: bytes[59]):
    bytes[32] user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    # proposal id will be calculated off-chain. it is base32 string representation of CID (base32 - cidv1 - raw - (sha2-256 : 256 : <metadata>))
    bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)

    int voting_start_timestamp = get_voting_start_timestamp()
    int voting_end_timestamp = voting_start_timestamp + (app_global_get(VOTING_DURATION_KEY) * DAY)

    box<Proposal> proposal = CreateBox(proposal_box_name)
    proposal.index = app_global_get(PROPOSAL_INDEX_COUNTER_KEY)
    proposal.creation_timestamp = Global.LatestTimestamp
    proposal.voting_start_timestamp = voting_start_timestamp
    proposal.voting_end_timestamp = voting_end_timestamp
    app_global_put(PROPOSAL_INDEX_COUNTER_KEY, app_global_get(PROPOSAL_INDEX_COUNTER_KEY) + 1)

    bytes proposal_data
    _, proposal_data = box_get(proposal_box_name)
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,bool)"), proposal_id, proposal_data))
    log(Concat(method("create_proposal(address,byte[59])"), user_address, proposal_id))
    return
end

@public()
func cancel_proposal(proposal_id: bytes[59]):
    bytes[32] user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
    box<Proposal> proposal = OpenBox(proposal_box_name)

    assert(Global.LatestTimestamp < proposal.voting_end_timestamp)
    assert(proposal.is_cancelled == BYTES_FALSE)
    proposal.is_cancelled = BYTES_TRUE

    bytes proposal_data
    _, proposal_data = box_get(proposal_box_name)
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,bool)"), proposal_id, proposal_data))
    log(Concat(method("cancel_proposal(address,byte[59])"), user_address, proposal_id))
    return
end

@public()
func cast_vote(proposal_id: bytes[59], votes_as_percentage: bytes, asset_ids: bytes, account_power_index: bytes):
    bytes[32] user_address = Txn.Sender
    bytes proposal_box_name = Concat(PROPOSAL_BOX_PREFIX, proposal_id)
    box<Proposal> proposal = OpenBox(proposal_box_name)

    # Proposal Active
    # proposal.voting_start_timestamp <= Global.LatestTimestamp < proposal.voting_end_timestamp
    assert(proposal.voting_start_timestamp <= Global.LatestTimestamp)
    assert(Global.LatestTimestamp < proposal.voting_end_timestamp)
    assert(proposal.is_cancelled == BYTES_FALSE)

    # vote count and asset count must be equal
    assert(len(votes_as_percentage))
    assert(len(asset_ids) == len(votes_as_percentage))
    assert(!(len(votes_as_percentage) % 8))
    int option_count = len(votes_as_percentage) / 8
    assert(option_count <= MAX_OPTION_COUNT)

    int proposal_index = proposal.index
    check_and_set_user_as_voted(user_address, proposal_index)

    int account_voting_power = get_account_voting_power_at(user_address, proposal.creation_timestamp, account_power_index)
    assert(account_voting_power)

    # Update proposal
    proposal.vote_count = proposal.vote_count + 1
    proposal.voting_power = proposal.voting_power + account_voting_power

    int option_vote_amount
    int asset_id
    int total_percentage = 0
    int tmp_index
    int tmp_vote_percentage
    for i in 0:option_count:
        tmp_index = i * 8

        # validate percentages
        tmp_vote_percentage = extract_uint64(votes_as_percentage, tmp_index)
        assert(tmp_vote_percentage)
        total_percentage = total_percentage + tmp_vote_percentage
        assert(total_percentage <= 100)

        # update vote amounts
        option_vote_amount = (tmp_vote_percentage * account_voting_power) / 100
        asset_id = extract_uint64(asset_ids, tmp_index)
        add_vote_amount(proposal_index, asset_id, option_vote_amount)

        log(Concat(method("vote(uint64,uint64,uint64)"), itob(asset_id), itob(option_vote_amount), itob(tmp_vote_percentage)))
    end
    assert(total_percentage == 100)

    bytes proposal_data
    _, proposal_data = box_get(proposal_box_name)
    log(Concat(method("proposal(byte[59],uint64,uint64,uint64,uint64,uint64,uint64,bool)"), proposal_id, proposal_data))
    log(Concat(method("cast_vote(address,byte[59],uint64)"), user_address, proposal_id, itob(account_voting_power)))
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
func set_voting_delay(new_voting_delay: int):
    bytes[32] user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    app_global_put(VOTING_DELAY_KEY, new_voting_delay)
    log(Concat(method("set_voting_delay(uint64)"), itob(new_voting_delay)))
    return
end

@public()
func set_voting_duration(new_voting_duration: int):
    bytes[32] user_address = Txn.Sender
    assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

    assert(new_voting_duration)
    app_global_put(VOTING_DURATION_KEY, new_voting_duration)
    log(Concat(method("set_voting_duration(uint64)"), itob(new_voting_duration)))
    return
end

@public()
func set_manager(new_manager: bytes[32]):
    bytes[32] user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))

    app_global_put(MANAGER_KEY, new_manager)
    log(Concat(method("set_manager(address)"), new_manager))
    return
end

@public()
func set_proposal_manager(new_proposal_manager: bytes[32]):
    bytes[32] user_address = Txn.Sender
    assert(user_address == app_global_get(MANAGER_KEY))

    app_global_put(PROPOSAL_MANAGER_KEY, new_proposal_manager)
    log(Concat(method("set_proposal_manager(address)"), new_proposal_manager))
    return
end

@public()
func get_box(box_name: bytes) bytes, bytes:
    bytes data
    _, data = box_get(box_name)
    # Return length as 2 bytes instead of using 8 bytes for uint64
    bytes length = extract(6, 2, itob(len(data)))
    return length, data
end


func check_and_set_user_as_voted(address: bytes[32], proposal_index: int):
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

func add_vote_amount(proposal_index: int, asset_id: int, vote_amount: int):
    bytes box_name = Concat(VOTE_BOX_PREFIX, Concat(itob(proposal_index), itob(asset_id)))
    _ = box_create(box_name, PROPOSAL_ASSET_BOX_SIZE)
    bytes proposal_asset_vote_amount
    _, proposal_asset_vote_amount = box_get(box_name)

    box_put(box_name, itob(btoi(proposal_asset_vote_amount) + vote_amount))
    return
end

func get_voting_start_timestamp() int:
    int end_timestamp_of_day = ((Global.LatestTimestamp / DAY) * DAY) + DAY
    return end_timestamp_of_day + (app_global_get(VOTING_DELAY_KEY) * DAY)
end

# Inner Txns

func get_account_voting_power_at(address: bytes[32], snapshot_timestamp: int, account_power_index: bytes) int:
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
