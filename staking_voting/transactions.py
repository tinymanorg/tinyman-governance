from algosdk import transaction
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from common.constants import STAKING_VOTING_APP_ID, VAULT_APP_ID
from common.utils import itob, get_account_power_index_at, parse_box_staking_proposal, get_required_minimum_balance_of_box
from vault.constants import ACCOUNT_POWER_BOX_ARRAY_LEN
from vault.transactions import prepare_budget_increase_txn
from staking_voting.constants import PROPOSAL_BOX_PREFIX, ATTENDANCE_BOX_PREFIX


def prepare_create_proposal_txn_group(user_address, proposal_id, sp):
    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=STAKING_VOTING_APP_ID,
            app_args=["create_proposal", proposal_id],
            boxes=[
                (0, proposal_box_name),
            ]
        )
    ]
    return txn_group


def prepare_cancel_proposal_txn_group(user_address, proposal_id, sp):
    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=STAKING_VOTING_APP_ID,
            app_args=["cancel_proposal", proposal_id],
            boxes=[
                (0, proposal_box_name),
            ]
        )
    ]
    return txn_group


def prepare_cast_vote_txn_group(ledger, user_address, proposal_id, votes, asset_ids, proposal_creation_timestamp, sp):
    assert (len(votes) == len(asset_ids))
    arg_votes = b"".join([itob(vote) for vote in votes])
    arg_asset_ids = b"".join([itob(asset_id) for asset_id in asset_ids])

    account_power_index = get_account_power_index_at(ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp)
    # assert account_power_index is not None
    account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
    proposal_index = parse_box_staking_proposal(ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])["index"]
    account_attendance_box_index = proposal_index // (1024 * 8)
    account_attendance_box_name = ATTENDANCE_BOX_PREFIX + decode_address(user_address) + itob(account_attendance_box_index)
    boxes = [
        (STAKING_VOTING_APP_ID, proposal_box_name),
        (STAKING_VOTING_APP_ID, account_attendance_box_name),
        *[(STAKING_VOTING_APP_ID, b"v" + itob(proposal_index) + itob(asset_id)) for asset_id in asset_ids],
        (VAULT_APP_ID, decode_address(user_address)),
        (VAULT_APP_ID, decode_address(user_address) + itob(account_power_box_index)),
        (VAULT_APP_ID, decode_address(user_address) + itob(account_power_box_index + 1)),
    ]
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=STAKING_VOTING_APP_ID,
            app_args=["cast_vote", proposal_id, arg_votes, arg_asset_ids, account_power_index],
            foreign_apps=[VAULT_APP_ID],
            boxes=boxes[:7]
        ),
    ]
    txn_group[0].fee *= 2

    if len(boxes) >= 7:
        txn_group.append(
            prepare_budget_increase_txn(user_address, sp=sp, index=VAULT_APP_ID, foreign_apps=[STAKING_VOTING_APP_ID], boxes=boxes[7:14]),
        )
    if len(boxes) >= 14:
        txn_group.append(
            prepare_budget_increase_txn(user_address, sp=sp, index=VAULT_APP_ID, foreign_apps=[STAKING_VOTING_APP_ID], boxes=boxes[14:]),
        )

    payment_amount = 0
    if account_attendance_box_name not in ledger.boxes[STAKING_VOTING_APP_ID]:
        payment_amount += get_required_minimum_balance_of_box(account_attendance_box_name, 24)

    for asset_id in asset_ids:
        box_name = itob(proposal_index) + itob(asset_id)
        if box_name not in ledger.boxes[STAKING_VOTING_APP_ID]:
            payment_amount += get_required_minimum_balance_of_box(box_name, 8)

    if payment_amount:
        txn_group = [
            transaction.PaymentTxn(
                sender=user_address,
                sp=sp,
                receiver=get_application_address(STAKING_VOTING_APP_ID),
                amt=payment_amount,
            )
        ] + txn_group
    return txn_group


def prepare_set_manager_txn_group(user_address, new_manager_address, sp):
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=STAKING_VOTING_APP_ID,
            app_args=["set_manager", decode_address(new_manager_address)],
        ),
    ]

    return txn_group


def prepare_set_proposal_manager_txn_group(user_address, new_manager_address, sp):
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=STAKING_VOTING_APP_ID,
            app_args=["set_proposal_manager", decode_address(new_manager_address)],
        ),
    ]

    return txn_group

