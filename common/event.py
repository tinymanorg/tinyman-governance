from dataclasses import dataclass

from Cryptodome.Hash import SHA512
from algosdk import abi
from algosdk.abi import ArrayStaticType
from algosdk.abi.byte_type import ByteType


@dataclass
class Event:
    name: str
    args: [abi.Argument]

    @property
    def signature(self):
        arg_string = ",".join(str(arg.type) for arg in self.args)
        event_signature = "{}({})".format(self.name, arg_string)
        return event_signature

    @property
    def selector(self):
        sha_512_256_hash = SHA512.new(truncate="256")
        sha_512_256_hash.update(self.signature.encode("utf-8"))
        selector = sha_512_256_hash.digest()[:4]
        return selector

    def decode(self, log):
        selector, event_data = log[:4], log[4:]
        assert self.selector == selector

        data = {
            "event_name": self.name
        }
        start = 0
        for arg in self.args:
            end = start + arg.type.byte_len()
            value = event_data[start:end]
            if isinstance(arg.type, type(ArrayStaticType(ByteType, 16))):
                data[arg.name] = int.from_bytes(value, 'big')   # btoi(value)

            else:
                data[arg.name] = arg.type.decode(value)
            start = end
        return data

def get_event_by_log(log, events):
    event_selector = log[:4]
    events_filtered = [event for event in events if event.selector == event_selector]
    assert len(events_filtered) == 1
    event = events_filtered[0]
    return event

def decode_logs(logs, events):
    decoded_logs = []

    for log in logs:
        event = get_event_by_log(log, events)
        decoded_logs.append(event.decode(log))

    return decoded_logs
