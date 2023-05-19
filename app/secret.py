#!/usr/bin/env python3
from .lib2 import PersistentMixin, StatefulSetMixin, Operator

class Secret(Operator):
    GROUP = "codemowers.io"
    VERSION = "v1alpha1"
    SINGULAR = "Secret"
    PLURAL = "Secrets"

    @classmethod
    def get_instance_properties(cls):
        return super(Secret, cls).get_instance_properties() + [
            ("size", {"default": 32, "type": "integer", "description": "Generated secret length"}),
            ("mapping", { "type": "array", "items": { "type": "object", "properties": {
                "key": { "type": "string", "description": "Secret key" },
                "value": { "type": "string", "description": "Secret value with suitable placeholders" }}}})
        ]

    async def reconcile(self):
        api_client = client.ApiClient()
        v1 = client.CoreV1Api(api_client)

        # Construct secret for cluster secrets
        sec = Secret(namespace, name)
        body = sec.wrap(body["spec"]["mapping"])
        kopf.append_owner_reference(body)
        try:
            await v1.create_namespaced_secret(namespace, client.V1Secret(**body))
        except ApiException as e:
            if e.status == 409:
                logging.info("Secret %s/%s already generated" % (namespace, sec.name))
            else:
                raise
        else:
            logging.info("Created secret %s/%s" % (namespace, sec.name))
        return {"state": "READY"}

if __name__ == "__main__":
    Secret().run()
