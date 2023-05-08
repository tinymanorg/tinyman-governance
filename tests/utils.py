

def itob(value):
    """ The same as teal itob - int to 8 bytes """
    return value.to_bytes(8, 'big')


def btoi(value):
    return int.from_bytes(value, 'big')


def sign_txns(txns, secret_key):
    return [txn.sign(secret_key) for txn in txns]
