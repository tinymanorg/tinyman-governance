#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@483cb7a30912747814c5ee0ee0dd7a7b1684c5f5

struct Proposal:
    creation_time: int
    voting_start_time: int
    voting_end_time: int
    option_count: int
    vote_count: int
    is_cancelled: int
    is_executed: int
    proposer: bytes[32]
    votes: bytes[128]
end

# struct Votes:
#     option_0_count: int
#     option_1_count: int
#     option_2_count: int
#     option_3_count: int
#     option_4_count: int
#     option_5_count: int
#     option_6_count: int
#     option_7_count: int
#     option_8_count: int
#     option_9_count: int
#     option_10_count: int
#     option_11_count: int
#     option_12_count: int
#     option_13_count: int
#     option_14_count: int
#     option_15_count: int
# end

# 2^64 - 1
const int MAX_UINT64 = 18446744073709551615
# 24 * 60 * 60
const int DAY = 86400
# 7 * DAY
const int WEEK = 604800
const int MAX_OPTION_COUNT = 16
# 8 * 16
const int VOTES_SIZE = 128
const int VOTES_SIZE_HALF = 64
const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes BYTE_ZERO_128 = "\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes PROPOSALS = "proposals"
const bytes VOTES = "votes"
const bytes TINY_ASSET_ID_KEY = "tiny_asset_id"
const bytes LOCKING_APP_ID_KEY = "locking_app_id"
const bytes PROPOSAL_THRESHOLD_KEY = "proposal_threshold"
const bytes PROPOSAL_ID_COUNTER_KEY = "proposal_id_counter"
const bytes PROPOSAL_MIN_LOCKED_AMOUNT_KEY = "proposal_min_locked_amount"
const bytes PROPOSAL_MIN_LOCK_TIME_KEY = "proposal_min_lock_time"
const bytes VOTING_DELAY_KEY = "voting_delay"
const bytes PROPOSAL_THRESHOLD_KEY = "voting_period"

if !Txn.ApplicationID:
    jump create_app
end

switch Txn.OnCompletion:
    NoOp: main
    OptIn: fail
    CloseOut: fail
    UpdateApplication: fail
    DeleteApplication: fail
end

block fail:
    exit(0)
end

block create_app:
    app_global_put(TINY_ASSET_ID_KEY, Txn.Assets[0])
    app_global_put(LOCKING_APP_ID_KEY, Txn.Applications[1])
    app_global_put(PROPOSAL_ID_COUNTER_KEY, 0)
    app_global_put(PROPOSAL_MIN_LOCKED_AMOUNT_KEY, 0)
    app_global_put(PROPOSAL_MIN_LOCK_TIME_KEY, WEEK)

    # Minimum amount to able to create proposal
    app_global_put(PROPOSAL_THRESHOLD_KEY, 5)

    # Voting Delay
    app_global_put(VOTING_DELAY_KEY, 2)

    # Voting Period
    app_global_put(PROPOSAL_THRESHOLD_KEY, WEEK)
    exit(1)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "create_proposal": create_proposal
        "cast_vote": cast_vote
    end
        # TODO:
        # "has_voted": has_voted
        # "get_state": get_state

    block create_proposal:
        bytes user_address = Txn.Sender

        # proposal id will be calculate off-chian. it is 32 bit hash of the proposal data.
        bytes proposal_id = Txn.ApplicationArgs[1]
        int option_count = btoi(Txn.ApplicationArgs[2])
        bytes account_power_box_index = Txn.ApplicationArgs[3]
        assert(len(proposal_id) ==  32)
        assert(!proposal_exists(proposal_id))
        assert(has_enough_power_to_create_proposal(user_address, account_power_box_index)) 

        int voting_start_time = get_start_time_of_day(Global.LatestTimestamp) + (app_global_get(VOTING_DELAY_KEY) * DAY)
        int voting_end_time = voting_start_time + app_global_get(PROPOSAL_THRESHOLD_KEY)

        box<Proposal> proposal = CreateBox(concat(PROPOSALS, proposal_id))
        proposal.creation_time = Global.LatestTimestamp
        proposal.proposer = Txn.Sender
        proposal.voting_start_time = voting_start_time
        proposal.voting_end_time = voting_end_time
        proposal.option_count = option_count

        app_global_put(PROPOSAL_ID_COUNTER_KEY, app_global_get(PROPOSAL_ID_COUNTER_KEY) + 1)

        # TODO: Add event log.
        exit(1)
    end

    block cast_vote:
        bytes user_address = Txn.Sender
        bytes proposal_id = Txn.ApplicationArgs[1]
        bytes raw_votes = Txn.ApplicationArgs[2]
        bytes account_power_box_index = Txn.ApplicationArgs[3]

        # Mark user as voted
        assert(box_create(concat(proposal_id, user_address), 0))

        bytes proposal_box_name = concat(PROPOSALS, proposal_id)
        box<Proposal> proposal = OpenBox(proposal_box_name)
        int option_count = proposal.option_count
        int expected_votes_len = option_count * 8
        assert(expected_votes_len == len(raw_votes))

        int total_percentage = 0
        for i in 0:option_count:
            total_percentage = total_percentage + extract_uint64(raw_votes, (i * 8))
            assert(total_percentage <= 100)
        end
        assert(total_percentage == 100)

        int account_voting_power = get_account_voting_power(user_address, proposal.voting_start_time, account_power_box_index)
        proposal.votes = add_votes(proposal.votes, raw_votes, account_voting_power, option_count)
        proposal.vote_count = proposal.vote_count + 1

        # TODO: Add event log.
        exit(1)
    end
end

func get_account_voting_power(address: bytes, snapshot_time: int, account_power_box_index: bytes) int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_tiny_power_of_at"
        ApplicationArgs[1]: itob(snapshot_time)
        ApplicationArgs[2]: account_power_box_index
        Accounts[0]: address
        Fee: 0
    end

    # tiny_power+delegatee
    int voting_power = extract_uint64(Itxn.LastLog, 0)
    return voting_power
end

func get_total_voting_power(snapshot_time: int) int:
    inner_txn:
        TypeEnum: Appl
        ApplicationID: app_global_get(LOCKING_APP_ID_KEY)
        ApplicationArgs[0]: "get_total_tiny_power_at"
        ApplicationArgs[1]: itob(snapshot_time)
        Fee: 0
    end

    # tiny_power+delegatee
    int voting_power = extract_uint64(Itxn.LastLog, 0)
    return voting_power
end

func has_enough_power_to_create_proposal(user_address: int, account_power_box_index: bytes) int:
    int threshold_percentage = app_global_get(PROPOSAL_THRESHOLD_KEY)
    int account_voting_power = get_account_voting_power(user_address, Global.LatestTimestamp, account_power_box_index)
    int total_voting_power = get_total_voting_power(Global.LatestTimestamp)

    # account_voting_power >= total_voting_power * threshold / 100
    # op-cost optimized
    int x_high
    int x_low
    int y_high
    int y_low
    x_high, x_low = mulw(account_voting_power, 100)
    y_high, y_low = mulw(total_voting_power, threshold_percentage)
    return (x_high > y_high) || ((x_high == y_high) && (x_low >= y_low))
end

func proposal_exists(proposal_id: bytes) int:
    int exists
    exists, _ = box_len(concat(PROPOSALS, proposal_id))
    return exists
end

func add_votes(old_votes: bytes, raw_votes: bytes, account_voting_power: int, option_count: int) bytes:
    bytes new_votes = bzero(option_count * 8) b| (raw_votes b* itob(account_voting_power / 100))
    bytes padded_votes = concat(new_votes, bzero(VOTES_SIZE - len(new_votes)))
    # max byte math size is 64.
    bytes high = bzero(64) b| (extract(0, 64, old_votes) b+ extract(0, 64, padded_votes))
    bytes low = bzero(64) b| (extract(64, 64, old_votes) b+ extract(64, 64, padded_votes))
    bytes votes = concat(high, low)
    assert(len(votes) == VOTES_SIZE)
    return votes
end

func get_start_time_of_day(time: int) int:
    return ((time / DAY) * DAY)
end

func get_start_time_of_next_day(time: int) int:
    return (((time / DAY) * DAY) + DAY)
end
