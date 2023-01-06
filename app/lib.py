import string
import random
from base64 import b64encode
from passlib.context import CryptContext

bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def parse_capacity(s):
    """
    Assumes the string is already validated by CRD
    """
    if s[-1] == "i":
        s = s[:-1]
        m = 1024
    else:
        m = 1000
    v, p = int(s[:-1]), s[-1]
    return v * m ** {"M": 2, "G": 3, "T": 4, "P": 5}[p]


assert parse_capacity("5M") == 5 * 10 ** 6
assert parse_capacity("5Mi") == 5 * 2 ** 20


def make_selector(application_name, instance_name):
    """
    Build labels and label selector for application/instance
    """
    labels = {
        "app.kubernetes.io/name": application_name,
        "app.kubernetes.io/instance": instance_name
    }

    expressions = []
    for key, value in labels.items():
        expressions.append({
            "key": key,
            "operator": "In",
            "values": [value]
        })

    selector = {
        "matchExpressions": expressions
    }
    return labels, selector


class Secret(object):
    def __init__(self, namespace, name, value=None, size=32):
        self.namespace = namespace
        self.name = name
        self.size = size
        self._value = value

    @property
    def value(self):
        if not self._value:
            self._value = "".join([random.choice(string.ascii_letters + string.digits) for j in range(self.size)])
        return self._value

    def __getitem__(self, key):
        if key == "namespace":
            return self.namespace
        elif key == "name":
            return self.name
        elif key in ("plaintext", "password"):
            return self.value
        elif key == "bcrypt":
            return bcrypt_context.hash(self.value)

    def wrap(self, mapping):
        data = {}
        for o in mapping:
            data[o["key"]] = b64encode((o["value"] % self).encode("ascii")).decode("ascii")
        kwargs = {
            "api_version": "v1",
            "data": data,
            "kind": "Secret",
            "metadata": {
                "name": self.name
            }
        }
        return kwargs


s = Secret("foo", "bar")
assert s["namespace"] == "foo"
assert s["name"] == "bar"
assert len(s["plaintext"]) == 32
assert len(s["bcrypt"]) == 60
