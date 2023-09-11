#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@e8d1b27620220bc4e520d7d3b6d62523e13a7723

# Name: <proposal_id>
# Size: 64 bytes
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
const bytes BYTES_ZERO = "\x00"
const bytes BYTES_ONE = "\x01"

# Global States
const bytes VAULT_APP_ID_KEY = "vault_app_id"
const bytes PROPOSAL_ID_COUNTER_KEY = "proposal_id_counter"
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
    app_global_put(VAULT_APP_ID_KEY, Txn.ApplicationArgs[0])
    app_global_put(PROPOSAL_ID_COUNTER_KEY, 0)

    app_global_put(VOTING_DELAY_KEY, 2)
    app_global_put(VOTING_DURATION_KEY, 7)

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
        "create_proposal": create_proposal
        "cast_vote": cast_vote
        "cancel_proposal": cancel_proposal
        "set_manager": set_manager
        "set_proposal_manager": set_proposal_manager
        "increase_budget": increase_budget
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
        assert(proposal.is_cancelled == BYTES_ZERO)
        proposal.is_cancelled = BYTES_ONE

        # TODO: Add event log
        exit(1)
    end

    block create_proposal:
        bytes user_address = Txn.Sender
        assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))

        # proposal id will be calculate off-chain. it is 32 bit hash of the proposal data.
        bytes proposal_id = Txn.ApplicationArgs[1]
        assert(len(proposal_id) ==  32)
        assert(!proposal_exists(proposal_id))

        int voting_start_timestamp = get_start_timestamp_of_day(Global.LatestTimestamp) + (app_global_get(VOTING_DELAY_KEY) * DAY)
        int voting_end_timestamp = voting_start_timestamp + (app_global_get(VOTING_DURATION_KEY) * DAY)

        box<Proposal> proposal = CreateBox(concat(PROPOSAL_BOX_PREFIX, proposal_id))
        proposal.index = app_global_get(PROPOSAL_ID_COUNTER_KEY)
        proposal.creation_timestamp = Global.LatestTimestamp
        proposal.voting_start_timestamp = voting_start_timestamp
        proposal.voting_end_timestamp = voting_end_timestamp
        # creation_timestamp: int
        # voting_start_timestamp: int
        # voting_end_timestamp: int
        # asa_count: int
        # vote_count: int
        # is_cancelled: int
        
        app_global_put(PROPOSAL_ID_COUNTER_KEY, app_global_get(PROPOSAL_ID_COUNTER_KEY) + 1)

        # TODO: Add event log.
        exit(1)
    end

    block cast_vote:
        bytes user_address = Txn.Sender
        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes votes_as_percentage = Txn.ApplicationArgs[2]
        bytes asset_ids = Txn.ApplicationArgs[3]
        bytes account_power_index = Txn.ApplicationArgs[4]
      
        bytes proposal_box_name = concat(PROPOSAL_BOX_PREFIX, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)

        # Proposal Active
        # proposal.voting_start_timestamp <= Global.LatestTimestamp < proposal.voting_end_timestamp
        assert(proposal.voting_start_timestamp <= Global.LatestTimestamp)
        assert(Global.LatestTimestamp < proposal.voting_end_timestamp)
        assert(proposal.is_cancelled == BYTES_ZERO)

        # vote count and asset count must be equal
        assert(len(votes_as_percentage))
        assert(len(asset_ids) == len(votes_as_percentage))
        assert(!(len(votes_as_percentage) % 8))
        int option_count = len(votes_as_percentage) / 8
        assert(option_count <= MAX_OPTION_COUNT)

        int proposal_index = proposal.index
        set_user_as_voted(user_address, proposal_index)

        int account_voting_power = get_account_voting_power(user_address, proposal.voting_start_timestamp, account_power_index)

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
            option_vote_amount = tmp_vote_percentage * (account_voting_power / 100)
            asset_id = extract_uint64(asset_ids, tmp_index)
            add_vote_amount(proposal_index, asset_id, option_vote_amount)
        end
        assert(total_percentage == 100)

        # TODO: Add event log.
        log(itob(Global.OpcodeBudget))
        exit(1)
    end
end

func get_account_voting_power(address: bytes, snapshot_timestamp: int, account_power_index: bytes) int:
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

func proposal_exists(proposal_id: bytes) int:
    int exists
    exists, _ = box_len(concat(PROPOSAL_BOX_PREFIX, proposal_id))
    return exists
end

func set_user_as_voted(address: bytes, proposal_index: int):
    int box_index = proposal_index / (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    int array_index = proposal_index % (ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE * 8)
    bytes box_name = concat(ATTENDANCE_BOX_PREFIX, concat(address, itob(box_index)))

    bytes sheet
    _ = box_create(box_name, ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE)
    _, sheet = box_get(box_name)
    assert(!getbit(sheet, array_index))
    sheet = setbit(sheet, array_index, 1)
    box_put(box_name, sheet)
    return
end

func add_vote_amount(proposal_index: int, asset_id: int, vote_amount: int):
    bytes box_name = concat(VOTE_BOX_PREFIX, concat(itob(proposal_index), itob(asset_id)))
    _ = box_create(box_name, PROPOSAL_ASSET_BOX_SIZE)
    bytes proposal_asset_vote_amount
    _, proposal_asset_vote_amount = box_get(box_name)

    box_put(box_name, itob(btoi(proposal_asset_vote_amount) + vote_amount))
    return
end

func get_start_timestamp_of_day(timestamp: int) int:
    return ((timestamp / DAY) * DAY)
end
