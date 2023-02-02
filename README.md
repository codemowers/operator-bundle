# operator-bundle

This operator bundle wraps numerous operators for
even more simplified workflows.

This operator allows you to implement different topologies:

* Database cluster outside Kubernetes cluster, just populate the secret for the operator
* Shared database cluster in the origin namespace
* Dedicated database clusters in the origin namespace
* Shared database cluster in dedicated namespace, secret with access credentials gets written back to origin namespace
* Dedicated database clusters in dedicated namespace, same secret behaviour

The actual implementation configuration referred by the `class` attribute varies
from cluster to cluster, but you should expect something like in the snippets
provided below

# Relational databases

To order MySQL or Postgres database, note the `capacity` ends up as persistent volume size
for dedicated clusters, in case of shared clusters it has no effect:

```
---
apiVersion: codemowers.io/v1alpha1
kind: MysqlDatabase
metadata:
  name: example
spec:
  capacity: 1Gi
  class: shared
---
apiVersion: codemowers.io/v1alpha1
kind: PostgresDatabase
metadata:
  name: example
spec:
  capacity: 1Gi
  class: shared
```

# Object storage

To order S3 bucket, note the `capacity` ends up as quota for the bucket:

```
---
apiVersion: codemowers.io/v1alpha1
kind: Bucket
metadata:
  name: example
spec:
  capacity: 1Gi
  class: shared
```

# Redis

We actually instantiate KeyDB multi-master cluster as it better fits the
Kubernetes world.
Note that you might want to distinguish `persistent` and `ephemeral` classes for Redis:

```
---
apiVersion: codemowers.io/v1alpha1
kind: Redis
metadata:
  name: example
spec:
  capacity: 512Mi
  class: ephemeral
---
apiVersion: codemowers.io/v1alpha1
kind: Redis
metadata:
  name: example
spec:
  capacity: 512Mi
  class: persistent
```

Note that `capacity` ends up as `maxmemory` configuration for the KeyDB cluster,
for persistent scenarios underlying persistent volume is allocated twice the amount
to accommodate BGSAVE behaviour.

In the persistent mode BGSAVE behaviour dumps the memory contents to disk
at least once per hour up to once per minute if there are more than 10000 write operations.
Note that AOF is not used in persistent mode so there is a chance to lose
writes which happened between BGSAVE operations.

You really should not use Redis as durable data storage.
