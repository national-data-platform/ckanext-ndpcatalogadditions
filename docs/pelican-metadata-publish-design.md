Pelican Metadata Publish Integration - Design

Owner: Rich
Status: Approved by Kai (2026-10-26). v1 receiver implemented and bench tested; Kai's code review (2026-11-20) addressed (section 5); real-origin testing pending.
Reviewers: Kai, Katie, Ismael, Dan. Replies from Kai and Ismael (2026-10-14 to 2026-10-26) are folded into the sections below; section 5 tracks what each question got back.

1. Summary

Pelican is adding a feature where, when a client uploads an object to a Pelican origin, the origin can publish an "object committed" event to an external metadata catalog over a signed webhook. The spec for this lives in pelican's own repo at docs/metadata-publish-design.md. I want ckanext-ndpcatalogadditions to be that external catalog for NDP, so that data landing in Pelican-federated storage shows up in the NDP catalog without someone having to manually submit it through the Portal.

This doc covers the design decisions for our side of that integration: how a Pelican event maps onto our existing Prekan/production CKAN model, what we trust and why, and what is explicitly out of scope for the first version.

2. Background

I started from pelican's metadata-publish-design.md, pinned at commit 6fac86f. It defines the webhook contract: three lifecycle events (object.committed, object.updated, object.deleted), a JWT-authenticated POST, a response contract (2xx accepted, 422 permanently rejected and stop retrying, anything else retried with backoff), and a note that catalogs discover the signing key from the federation's registry rather than needing to reach the origin directly.

That doc is generic - it describes what any catalog implementation needs to do, not what ours specifically should do. To fill in the NDP-specific half I went back to our own extension's design doc (Kai's doc, NDP CKAN extension Design Doc), which lays out the two-instance architecture: Prekan for staged submissions, production CKAN for the published catalog, with a curator-reviewed package_approve step promoting one to the other. I wanted this feature to fit that model rather than add a second, parallel way of getting data into the catalog.

A few of the open questions below turned out to be security questions, not just data-modeling ones, so I went and read the actual Pelican source docs (scopes.yaml, user-group-design.md) rather than assume how their token model works.

3. Decisions

3.1 Target is Prekan, going through the normal review path

Every existing way of getting a dataset into the NDP catalog goes through Prekan first, then a curator approves it into production. I decided Pelican-sourced submissions should go through the exact same path - land as an ordinary Prekan package, sit in the normal review queue, get promoted by package_approve like anything else.

To be clear about why: this is not because the Pelican JWT verification is somehow insufficient to trust on its own. It would be defensible to treat a verified Pelican event as trusted enough to skip review. I am choosing Prekan because that is simply the normal path for everything else in this catalog, and I would rather have one submission pipeline than two.

Kai agreed that routing through Prekan and review is right (2026-10-14). Nobody has argued for a bypass.

"Ordinary" includes visibility. In this extension private does not mean draft - Prekan itself is the review queue, and reviewers list everything in it. private means restricted to the Keycloak groups named in an existingGroupIds extra, and approval carries that restriction into production. So Pelican-sourced packages are created public (private false), like an ordinary submission, and get the full public validation at completion (3.4). Only the catalog entry is public; reading the data is still governed by Pelican's own access control. Restricted Pelican data is a follow-up - see open items.

3.2 namespace maps to org, directly, one to one

First question was what a Pelican namespace should bind to on our side - one of the existing catalog group types (user_catalog, pathfinder_catalog, datahub_catalog) or a plain CKAN org. I picked org.

I originally wanted the org to come from the uploader's metadata rather than be derived from the namespace, mostly because namespace path strings do not cleanly turn into CKAN org names and I did not want to hard-code a mechanical transformation if I did not have to.

That raised a real question: what stops someone from putting an arbitrary org name in that metadata and getting our code to create a dataset there. I went and checked the existing controller.py code for how org assignment works today (process_user_and_organization) and found a real gap, unrelated to this feature: any authenticated user hitting the existing /ndp/package_create endpoint can name any existing org, and the code will silently grant them editor membership on it with no check at all. That is not how native CKAN behaves - CKAN's own package_create auth requires real org membership or sysadmin, and our extension's logic/auth.py does not override that - so the gap is specific to this one bolted-on endpoint, not CKAN itself. Worth fixing on its own, separate from this work. Kai pointed out that our code also enforces a per-creator ownership check on update and delete, so someone granted editor this way still cannot edit or delete another person's dataset in that org; he is double-checking that the check is applied on every update/delete path. That narrows the gap to the create-time membership grant itself.

For the Pelican case specifically, I checked whether the JWT at least tells us who the uploading person is. It does not. Per Pelican's own scopes.yaml, the pelican.metadata scope is issued by the origin, not by the user - it proves the event came from an origin that is trusted for a given namespace, and says nothing about who actually uploaded the file. So an org name sitting in unauthenticated event metadata would have no backing at all, which is worse than the existing gap, not equivalent to it.

I then checked whether Pelican does real access control on who can write into a namespace in the first place, since if that holds, the namespace itself is something we can actually trust. It does: scopes like wlcg.storage.create and scitokens.write are path-scoped per namespace, and an origin will not accept a PUT from someone who does not already hold the right scope for that path. So no object.committed event can ever be generated for a namespace a given uploader was not already allowed into - that enforcement happens upstream of us, before we see a webhook call at all.

Given that each namespace maps to exactly one org for now (confirmed this directly, not assuming it), I decided to treat namespace as the org, directly, rather than building a separate allow-list layer. This inherits Pelican's own real access control for free instead of us trying to re-implement something equivalent. If namespaces stop being strictly one-to-one with orgs later, this needs to be revisited - see open items.

I am still keeping an explicit, admin-maintained namespace-to-org mapping rather than deriving the org name mechanically from the namespace string. Two reasons: an unmapped namespace should get a clean 422 instead of silently auto-creating an org, and namespace path strings do not necessarily make valid or sensible CKAN org names on their own. A mapped org that does not exist yet is handled the same way ordinary submissions handle it: copied from production if it exists there, otherwise created on first use.

Two consequences of trusting the namespace and nothing else. First, the object path in an event must sit under the event's namespace; one that does not is rejected, since the token only vouches for that namespace. Second, custom metadata says nothing verified about who uploaded it, so it cannot touch fields other parts of the extension rely on: ndp_creator_md5 (which backs the per-creator ownership check below), approval_status, existingGroupIds, our own pelican_* bookkeeping, or CKAN's core package fields (name, owner_org, private, state, maintainer, version, and so on). Those keys are dropped and logged; the rest of the event is still applied. The exceptions are the core fields that describe the data rather than control it - title, notes, tags, license_id, author and author_email - which uploaders may set (section 6).

3.3 dataset is an explicit field the uploader sets, not derived from path or namespace

I considered two defaults: one dataset per namespace (every object under a namespace becomes a resource on a single package), or one dataset per object (every committed object becomes its own package). Rejected both - one dataset per namespace does not let a project publish more than one distinct collection under its own org, and one dataset per object produces an unworkable number of near-empty records given the known issue (already flagged in our own design doc) that large per-user listing endpoints already struggle with too many records.

I also considered deriving the dataset boundary from the object path itself, for example treating the first path segment under a namespace as the dataset slug. Decided against that too - it would force every Pelican client to follow a specific directory convention. Simpler to just have the uploader set an explicit dataset field in their custom metadata, and use that as the package name within the org. The package is named <org>-<dataset>. As with ordinary submissions, a name that is already published in the production catalog is rejected up front rather than staged, since approving it would collide.

3.4 NDP-required fields are merged progressively across events, gated by an explicit completion count

I checked explicitly whether Pelican's protocol defines or reserves any of this, since I did not want to assume. It does not. Per the metadata-publish-design doc, Pelican only reserves four keys on the object: path, size, etag, created_at. Everything else is whatever key/value pairs the uploader attached via the X-Pelican-Object-Metadata header, which Pelican copies through as-is - unvalidated, uninterpreted, and not required by Pelican in any way. So the entire custom-field schema - dataset, title, notes, point of contact, whatever else NDP needs - is ours to define and publish to uploaders as a convention.

My first version of this had the first object.committed event for a new dataset create the package using whatever metadata happened to be on that one file. Kai flagged the problem with that (2026-10-14): Pelican emits one event per file, with no guaranteed ordering and no reliable concept of a "first" event, and dataset-level fields - in CKAN typically title, notes, tags, point of contact, and often spatial and temporal coverage - describe the whole dataset, not any single file. Requiring every uploader to repeat the full dataset record on every file is unworkable, and an incomplete one would just fail validation with nothing telling anyone why.

The fix: treat every event's custom fields as an incremental contribution instead of a one-shot declaration. The first object.committed for a new (namespace, dataset) pair creates the package using only what CKAN needs at minimum (a name built from the mapped org and the dataset field, <org>-<dataset>), without running the full ndp_package_validate yet. Every event after that merges whatever dataset-level fields it carries into the package - last write wins when the same field shows up more than once across events.

Completion is signaled by an expected_count field the uploader duplicates identically across every file's metadata for that dataset, so losing any one file does not lose the count. Once the number of resources on the package reaches expected_count, we run the full ndp_package_validate - if it passes, the dataset is ready for the normal curator review queue like anything else; if required fields are still missing at that point, it sits as a visibly incomplete draft (pelican_ingest_status and pelican_ingest_errors on the package) until it is fixed. Validation re-runs on every later event for the dataset, so the uploader can fix it by re-uploading any of its files with the missing keys attached (section 6.3). Otherwise it is fixed by hand, and in practice that means a sysadmin: the package's creator is the service account (3.8), and the per-creator ownership check lets only the creator or a sysadmin edit it through our endpoints. Reviewers can still approve or reject it as it stands.

Because packages are public (3.1), "required" means the public field set ndp_package_validate checks: title, notes, tags, and the extras uploadType, lastUpdateDate, pocName and pocEmail, plus a name, description, status and mimetype on every resource. The receiver fills the resource fields itself (status active, mimetype guessed from the object name); the dataset-level ones have to come from uploader metadata. That makes the uploader convention concrete: dataset and expected_count for us, title, notes, tags, license_id, author and author_email as CKAN fields, and every other key - including the four extras above - stored as a package extra of the same name. If the real file count ends up short of expected_count (a lost upload, a bad estimate), there is no automatic timeout - a human can manually move it forward regardless of the count. Going over the declared count is not specially handled for now - additional files beyond expected_count are just accepted.

Kai called expected_count a promising approach (2026-10-19). He also pointed out, from the Pelican resources already in the catalog, that completeness can depend on resources that never generate a Pelican event - at least one existing dataset mixes Pelican objects with a GitHub link. As implemented, the count is the number of resources on the package, whatever their source, so a non-Pelican resource a submitter adds by hand counts toward expected_count. That is workable but means expected_count is really "total resources", not "Pelican files", and uploaders need to be told which.

Approval and rejection both delete the Prekan copy. An event that arrives for a dataset after that is rejected with a 422 ("already been reviewed"), rather than quietly updating the deleted copy and answering 2xx while the file never reaches the published dataset. Adding files to a reviewed dataset is a curator action in v1.

Approval copies the package to production as is, so it strips the receiver's Prekan-only bookkeeping first: pelican_ingest_status, pelican_ingest_errors and pelican_expected_count. pelican_namespace and pelican_dataset stay on the published dataset as provenance (decided after Kai's code review, 2026-11-20).

Concurrent events. Kai's code review (2026-11-20) pointed out that concurrent events for one dataset are the normal case, not an edge case: a recursive upload (pelican object put -r) transfers about five files at once (Client.WorkerCount), and the origin makes a synchronous publish attempt as each file commits. Every event reads the whole package, adds its resource and saves the whole package back, and Prekan runs several workers. Two events at once would both save N+1 resources, one file would silently drop out with a 2xx, and the dataset could never reach expected_count. Two first events would both try to create the package, and the second would get a 422 and be lost. CKAN's resource_create does not avoid this; it does the same read-modify-write internally.

So each event holds a per-dataset lock from the read (or create) through the save: a PostgreSQL advisory lock keyed on the package name, taken on its own database connection because CKAN actions commit mid-request. An event that can't get the lock within 5 seconds gets a 503 and Pelican retries it. The wait is kept short because a waiting request holds a web worker. A test with five concurrent appends lost four of the five resources without the lock and none with it.

The cost is throughput. Saves for one dataset now run one at a time, and each save rewrites the whole package and reindexes it in Solr, so it gets slower as the dataset grows. Timings on the laptop bench (CKAN 2.10, one event, including about 0.3 s of client start-up): about 0.5 to 1 s with 50 to 200 resources, about 3.5 s with 1000. That is roughly 3 ms per resource, so Pelican's 10-second per-attempt timeout would be reached around 3000 resources; Prekan's numbers will differ. Ten files of one dataset sent at once on the bench: six were accepted within the 5-second wait, four got a 503, and all ten were in place after one retry, in path order.

In Pelican's default eventual mode that is acceptable. Uploads succeed regardless of the catalog, non-2xx answers are retried in the background, and processing is idempotent by resource url (3.6), so a retry, or an attempt that timed out after its save completed, just reapplies the same state. In transactional mode (Origin.Metadata.Mode), a 503 or a timeout would fail the upload itself, so this design is not suitable for transactional origins as it stands. Logical-unit resources (3.6) cut the resource count, and with it the save time, for datasets with many files. If that is not enough, the next step is accept-and-queue: verify and check the event, put it on a durable queue, answer 2xx, and apply events per dataset in batches with one save. It is not built yet (section 4).

3.5 v1 scope: object.committed only, json body only

Pelican's spec defines three lifecycle events (object.committed, object.updated, object.deleted) plus a multipart variant for attaching an opaque metadata blob alongside the json event. I'm scoping v1 to object.committed with a plain json body only. object.updated and object.deleted both require Pelican's own local object-tracking subsystem to be turned on at the origin, and have different delivery semantics (always asynchronous, best effort) - more moving parts than I want to take on for a first version. The multipart/blob variant is deferred too - nothing in our metadata schema currently needs an attached binary manifest.

3.6 resource identity is keyed by the resource url, not the event id; files can be grouped into one resource

Pelican's delivery is at-least-once, and their doc says to dedup on the event id. Rather than stand up a new table just to track which event ids we have already seen - this extension deliberately keeps no local database today, everything lives in the two CKAN instances' postgres and solr, per our own design doc - I decided to make processing naturally idempotent instead. When an object.committed event comes in, look for an existing resource on the target package whose path matches the event's object path, and update it in place rather than append a new one. A redelivered event just reapplies the same state, so there is no separate bookkeeping needed, and the extension stays stateless. In the code, the match is on the resource url (3.7), which is built from the object path.

Logical units. Kai's code review (2026-11-20, section 5) argued that one resource per file is often the wrong unit. A shapefile is three to eight files that are only usable together, a Zarr store is thousands of chunk files, a partitioned Parquet table is many part files, and existing Pelican entries in the catalog are often registered as folders. A dataset with thousands of per-file resources is hard to browse and slow to save (3.4). So the uploader can optionally group files: every file carrying the same "resource" key (a folder path under the namespace) updates one resource that points at the folder. Its name is the folder path relative to the namespace, its mimetype is inode/directory, and its last_modified is the newest file seen. It has no size or hash, because one event per file cannot keep a total without double-counting redeliveries; a manifest file listed as its own resource covers per-file sizes and checksums. Files without the key stay per-file resources, which suits small datasets and files worth listing on their own, such as a README or data dictionary. Two more optional keys, "format" and "resource_description", set those fields on the file's resource, grouped or not (section 6).

Names and order. A per-file resource is named by its path relative to the namespace (run-1/data.csv), not the bare file name, so files with the same name in different folders stay distinguishable. A new resource is inserted in path order among the existing Pelican resources instead of appended, so the list follows the tree rather than arrival order. Existing resources are never moved, and name, description, mimetype and status are only defaults, so a curator's edits survive later events.

3.7 resource url format

Resources in CKAN are pointers only - this catalog never stores the underlying bytes, which is true everywhere else in this extension too. The resource url for a Pelican object is pelican://<federation-host><path>, with the host taken from the event's federation url and the path from the event's object. The path is already federation-rooted - it starts with the namespace - so the namespace is not prepended a second time; in the osg-htc.org federation, an object at /my-namespace/run-1/data.csv becomes pelican://osg-htc.org/my-namespace/run-1/data.csv. That is the form the Pelican client accepts directly and the pelican:// form already used in the catalog (Kai's list, 2026-10-19). A grouped resource (3.6) points at its folder, with a trailing slash: pelican://osg-htc.org/my-namespace/gis/roads/. An event whose federation is missing or not a url is rejected before anything is created. package_validation.py already recognizes the pelican:// scheme specifically and skips its url-liveness check for it, which makes sense since a pelican:// uri is not something our validator can http-get directly. This reuses existing, already-correct behavior instead of adding a new special case.

Most existing catalog entries use the other scheme, osdf:///<path>, which only names a path and relies on the client knowing the OSDF federation. pelican:// with the host is the self-describing one, and it works for any federation, not only OSDF.

3.8 creator identity is a dedicated service account

Since there is no per-uploader identity available anywhere in this flow (see 3.2), Prekan records created this way still need an owner. I'm using a single dedicated CKAN service user for this (something like pelican-ingest-bot), created once and added as editor on whichever orgs its namespaces map to, through the same process_user_and_organization mechanism used for human submissions today. Its ndp_creator_md5 will be a fixed value rather than one derived per upload, since there is no per-upload user id to hash in the first place.

Ismael raised the alternative (2026-10-22): since each Pelican origin has its own identity provider, map origin identities to NDP Keycloak users and link them during Prekan pre-approval, so records are not all owned by one account. I agree that is better if it can be done. It needs the uploader's identity to reach us, and per 3.2 the webhook token does not carry it today, so it depends on Pelican adding that (or on a separate lookup) and on the Keycloak/Pelican identity-linking work the epic explicitly leaves out of scope. The service account stays for v1; this is an open item.

3.9 response contract

Following Pelican's contract as specified: 2xx once our package_create or package_update call actually succeeds, 422 for anything permanently unprocessable, and anything else treated as a plain failure so Pelican retries with backoff instead of us claiming a false success. In practice that splits into five cases:

- 2xx: the event was applied. This includes the case where the dataset has reached expected_count but fails ndp_package_validate. Per 3.4 that is not a rejection of the event - the file itself landed fine - so the dataset is marked incomplete (pelican_ingest_status and pelican_ingest_errors on the package) and the event is still accepted. Returning 422 there would just make Pelican drop an event that was processed correctly.
- 422: the event itself can never succeed - a namespace not in our mapping, a missing dataset field, a missing object path, one outside the namespace or with empty, "." or ".." segments, a "resource" folder outside the namespace or not containing the object, a federation that is not a url, an unsupported event type, a body that is not a json object, a dataset that has already been reviewed, a dataset name already published in production, or data CKAN itself refuses (for example an invalid tag). Protected metadata keys (3.2) are dropped, not rejected.
- 401: the token did not verify - missing or malformed, expired, bad signature, wrong audience, missing pelican.metadata scope for the namespace, an untrusted federation, or signing keys that could not be fetched from the registry. Pelican retries these, which is the right outcome for the last one in particular, since a registry outage is transient.
- 503: another event for the same dataset held the lock for more than 5 seconds (3.4). Pelican retries.
- 500: our own side failed - the receiver is not configured (no trusted federation or webhook url) or something downstream errored, such as the remote org lookup timing out, or the production name check getting anything but a clear answer (an outage's 502 is not "name available"). Unexpected errors land here too, rather than as a 422, so they are retried. Pelican retries, and the same event succeeds once the problem is fixed.

Routes. The receiver answers at POST /ndp/pelican_metadata_publish. It is also bound to POST / on Prekan, because the test origin was given the bare site URL as its webhook. That shares Prekan's site root: CKAN's own / route is GET-only, so nothing is displaced, and JWT verification still guards it, but it is easy to miss in later maintenance (Kai's review 2.2). The / rule should be removed once origins are configured with the specific path (section 4).

4. Open items

- object.updated and object.deleted handling, for whenever we want full lifecycle coverage.
- the multipart/blob variant, if a project ends up needing to attach an opaque manifest.
- a pre-existing, unrelated authz gap in process_user_and_organization (controller.py) that lets any authenticated user join any org through the existing /ndp/package_create endpoint. Found this while working through 3.2. Should get its own fix, independent of this feature. The per-creator ownership check limits the damage to the membership grant (see 3.2); Kai is confirming the check covers every update/delete path.
- restricted Pelican data: packages are public by default (3.1). Some namespaces hold restricted data (the existing catalog has osdf:///ndp/private/... entries). Supporting that means accepting private plus existingGroupIds from uploader metadata and validating the groups against Keycloak like ordinary submissions do - but those values would arrive unauthenticated as to the uploader, the same trust question as the org in 3.2. Not in v1.
- incomplete drafts can only be fixed by hand by a sysadmin (3.4), because the service account is the creator. Uploaders can still fix missing metadata by re-uploading a file with it attached (6.3), so this mostly matters for fixes that metadata can't make. Fine while volumes are small; per-uploader ownership (below) would let the uploader edit their own.
- folder resources: some existing catalog entries point at a folder (for example .../ucr_seis/Data_Salton, .../BCZO_SnowOn05/). Addressed by the optional "resource" key (3.6) when the uploader sets it. A folder resource has no total size or file count.
- one object in several datasets: the existing catalog has objects registered under more than one dataset. An event carries a single dataset field, so one upload lands in exactly one dataset; a second listing would have to be added by hand.
- finalized data versus testing (Ismael, 2026-10-22): how do we tell a finished data product from someone testing, and are there namespaces known to hold finalized products? Curator review in Prekan is the backstop, but test uploads to a mapped namespace will still create Prekan records. Options include only mapping namespaces that hold finalized data, or an explicit uploader flag; not decided.
- per-uploader ownership (Ismael, 2026-10-22): link Pelican origin identities to NDP Keycloak users instead of the single service account (see 3.8).
- no story yet for a namespace spanning more than one org. Current design assumes strict one-to-one and does not degrade gracefully if that stops being true.
- JWT verification is implemented (trusted federation from config, registry discovery and jwks fetched from the trusted federation only, both cached with a ttl) but not written up in detail here. This doc covers the data model and trust boundary decisions, not the verification code.
- accept-and-queue (3.4): needed if Prekan's save times make per-dataset serialization too slow, and for any origin running in transactional mode. Needs a durable queue and a worker on Prekan; CKAN-level rejections could then no longer be reported back to Pelican as 422s.
- remove the POST / route (3.9) once origins use /ndp/pelican_metadata_publish.
- transactional publish mode: not supported as it stands (3.4). Which mode the origins will run is an open question for the Pelican side.
- no automatic handling yet for a dataset that never reaches its expected_count (relies entirely on a human noticing and manually moving it forward) or for one that goes over it (extra files are just accepted with no alert).

5. Questions to Kai, Katie, Ismael, and Dan, and what came back

- Does namespace-to-org stay strictly one-to-one for every project we expect to onboard onto this, not just the ones I had in mind when I wrote this up.
  Answer so far: confirmed one-to-one for the namespaces currently in view (3.2). Not confirmed for every future project; still an open item.
- Any objection to Prekan-only for Pelican-sourced submissions, with no bypass of curator review.
  Answer: no objection. Kai agreed routing through Prekan/review is right (2026-10-14).
- Is the custom metadata field set I'm proposing (dataset, title, notes, point of contact, etc) the right one, or is there already a convention elsewhere I should be matching instead.
  Answer so far: no existing convention was pointed to. Kai listed the dataset-level fields a CKAN dataset typically carries - title, notes, tags, point of contact, and often spatial and temporal coverage (2026-10-14). The receiver maps title, notes, tags, license_id, author and author_email to the matching CKAN fields and stores everything else as package extras under the same name, so the convention is the field set ndp_package_validate already uses for ordinary public submissions (see 3.4 for the required ones; spatial and temporal coverage are the optional spatialCov/dataBbox and startDateTime/endDateTime extras it also validates). Section 6 is the uploader reference, with worked examples.
- Any concern with a single dedicated service account owning all Pelican-sourced Prekan records, or is there a reason to want something more granular.
  Answer: Ismael would rather link Pelican origin identities to NDP Keycloak users so records are not all one account (2026-10-22). Kept the service account for v1; see 3.8 and open items.

Kai's code review of the implementation (2026-11-20), and what was done:

- 2.1 concurrent events lose resources or the first file: fixed with a per-dataset lock and a 503 on timeout (3.4). Tests cover concurrent first events and concurrent appends; the append test fails without the lock.
- 2.2 POST / shares Prekan's site root: kept for now and documented (3.9, README, a comment in plugin.py). It goes once origins can use the specific path, which is open question 4 below.
- 2.3 event rate against save time: measured on the bench and written up in 3.4. Eventual mode is fine; transactional mode isn't supported yet. Accept-and-queue is the documented next step.
- 3.1 ValueError caught as 422: only PelicanRejectError is a 422 now. Unexpected errors are 500s.
- 3.2 any non-200 from production treated as "name available": only a 404 counts as available now. Anything else is a 500, so the event is retried.
- 3.3 pelican_* extras published on approval: bookkeeping extras stripped, provenance kept (3.4).
- 3.4 full-package save per event: serialized by the lock, reduced by logical-unit resources. Batching is part of accept-and-queue.
- 3.5 ".." segments: empty, "." and ".." segments are rejected in the namespace (401, with the other namespace checks), the object path and the resource folder (422).
- 3.6 branch based on main: rebased onto dev and tested there.
- Section 5, logical units: optional "resource", "format" and "resource_description" keys, path-relative names, path-ordered insertion (3.6).
- Open question 3, does put -r attach --metadata-file to every file: yes. Pelican's client (WithObjectMetadata, at the pinned commit) propagates the metadata map to every file of a recursive upload. So each put -r carries one record. A "resource" key in it groups the whole upload, so run one put -r per logical unit. The same code allows number and boolean values; the receiver stores them as text (true/false for booleans).
- Still open: which publish mode the origins will run (eventual or transactional), and whether the webhook URL can be changed to /ndp/pelican_metadata_publish.

Other input from the thread:

- Kai (2026-10-14): agreed with the overall approach, with one concern - the per-file event problem that led to the progressive merge in 3.4 (expected_count was my proposal back, 2026-10-19). Also the per-creator ownership check noted in 3.2.
- Kai (2026-10-19): a spreadsheet of the Pelican resources already registered in the NDP catalog, which surfaced the folder, multi-dataset, mixed-source and url-scheme items in section 4.
- Ismael (2026-10-22): the finalized-versus-testing question in section 4.
- Kai (2026-10-26): the ndp-test Prekan and CKAN instances to test against (see References). Their API takes a Keycloak token as a Bearer token in the Authorization header.

6. Uploader metadata reference and examples

This is the convention uploaders follow. Each file is uploaded with key/value pairs attached through Pelican's X-Pelican-Object-Metadata header (the header format is Pelican's, see its metadata-publish-design doc). Pelican copies them into the object block of the event it sends us, next to the four keys it sets itself (path, size, etag, created_at). Pelican allows string, number and boolean values; the receiver stores them as text (booleans as true/false). With pelican object put --metadata-file, the keys are the top-level fields of a JSON file. In a recursive upload (put -r) every file gets the same metadata.

Uploaders do not choose the org. It comes from the namespace the event was signed for (3.2), looked up in a map the NDP operators maintain on the Prekan side. Like the extension's other settings, the map is an environment variable on the CKAN container, holding a JSON object from namespace to CKAN org name:

    CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP={"/example-ns": "example-org", "/another-ns": "another-org"}

- The event's namespace must equal a key exactly; there is no prefix matching. Objects under /example-ns/snow/... arrive with namespace /example-ns, which is the key to list.
- A namespace that is not in the map gets a 422 ("unmapped namespace"), and the file is not catalogued.
- A mapped org that does not exist in Prekan yet is created automatically (3.2): copied from the production org of the same name if there is one, otherwise as a new org titled with the name. A typo in the map therefore creates a stray org rather than failing, so check the names against production when adding entries.
- Onboarding a namespace means adding an entry and recreating the container; no code change.

Keys the receiver uses itself (not stored as dataset metadata):

- dataset (required on every file): the dataset the file belongs to. The CKAN package is named <org>-<dataset>, where org is the one mapped to the namespace. Lowercase letters, digits, - and _.
- expected_count (send the same value on every file): how many resources the dataset should end up with. When the package reaches that many, full NDP validation runs. Non-Pelican resources a submitter adds by hand count too.

Keys that describe the file's resource rather than the dataset (optional, never stored as dataset metadata; see 3.6):

- resource: a folder path under the namespace, for example /example-ns/gis/roads. Every file with the same value lands on one resource pointing at that folder, instead of one resource per file. The file must sit inside the folder. Because put -r attaches the same metadata to every file, run one put -r per logical unit.
- format: the resource format, for example shapefile, zarr, parquet. CKAN normalizes names it knows (shapefile becomes SHP). Without it, CKAN fills format from the mimetype.
- resource_description: the resource's description. Without it, a file gets "Pelican object at <path>" and a folder "Pelican folder <path>/ - browse and fetch its files with Pelican tools".

Keys that become CKAN package fields:

- title (required)
- notes (required): the dataset description
- tags (required for public datasets): comma-separated, each tag at least 2 characters
- license_id: a CKAN license id, for example cc-by, cc-by-sa, cc-zero, odc-by, odc-odbl, other-open
- author, author_email: author_email must be a valid email address

Keys stored as package extras of the same name. Required for public datasets:

- uploadType: one of dataset, model, service, docker-image, application, code
- lastUpdateDate
- pocName
- pocEmail: must be a valid email address

Optional extras that NDP validates when present:

- publisherEmail, creatorEmail: email address
- publisherWebsite, creatorWebsite, pocWebsite, docsURL, datasetPageUrl: url
- doi: 10.xxxx/... or https://doi.org/10.xxxx/...
- spatialCovFormat (text, geojson or wkt) with spatialCov; with geojson or wkt the coverage must parse
- dataBboxFormat (string, geojson or wkt) with dataBbox; same rule
- spatialRes: starts with a number, for example "10 meter"
- startDateTime, endDateTime: date/time
- temporal: {"startTime": "<ISO8601>", "endTime": "<ISO8601>"}

Extras with behaviour attached: enable_contextual_insight=true makes approval trigger the Contextual Insights pipeline on the published dataset, as it does for ordinary submissions.

Any other key is stored as an extra without checks. Keys are case-sensitive, so a misspelled required key (pocemail) is stored under the wrong name and the dataset then fails validation for the missing one.

Keys that are dropped and logged: ndp_creator_md5, approval_status, existingGroupIds, anything starting with pelican_, and every other CKAN core field (name, owner_org, private, state, type, id, url, version, maintainer, maintainer_email, groups, resources, extras, tag_string). The rest of the event is still applied.

Three behaviours to know about:

- Apart from the three resource keys, metadata describes the dataset, not the file. A per-file resource is named by its path relative to the namespace (salton/readings.csv), and its mimetype is guessed from the extension. Name, description, mimetype and status are defaults, so a curator can edit them and later events do not overwrite those edits. An explicit format or resource_description from a later event does overwrite.
- A field CKAN itself refuses (a one-character tag, a malformed author_email) rejects that whole event with a 422, including its file. Pelican does not retry a 422, so the uploader has to fix the value and upload the file again.
- Files of one dataset are applied one at a time (3.4). When many arrive at once, some get a 503 and Pelican retries them; this is normal in Pelican's default eventual mode.

The examples below assume namespace /example-ns mapped to org example-org (the first entry in the map above) on federation osg-htc.org. Each was run against the bench with the fake Pelican, and the "In CKAN" blocks are trimmed package_show output from those runs. The service account's ndp_creator_md5 and the pelican_namespace/pelican_dataset extras, present on every package, are left out.

6.1 One file

One upload, carrying the full dataset record and expected_count 1.

    /example-ns/salton/readings.csv
      dataset=salton-readings
      expected_count=1
      title=Salton Sea Readings
      notes=Daily water-quality readings.
      tags=water,salton-sea
      license_id=cc-by
      author=Jane Doe
      author_email=jane@example.org
      uploadType=dataset
      lastUpdateDate=2026-11-19
      pocName=Jane Doe
      pocEmail=jane@example.org

The event body we receive:

    {
      "type": "object.committed",
      "federation": "https://osg-htc.org",
      "namespace": "/example-ns",
      "object": {
        "path": "/example-ns/salton/readings.csv",
        "size": 1024,
        "etag": "\"0979c8a00b2345ceae136e35f5aa2fbd\"",
        "created_at": "2026-11-19T21:30:02Z",
        "dataset": "salton-readings",
        "expected_count": "1",
        "title": "Salton Sea Readings",
        ...the remaining keys above, as strings...
      }
    }

In CKAN (Prekan), package example-org-salton-readings:

    title:        Salton Sea Readings
    notes:        Daily water-quality readings.
    private:      false
    license_id:   cc-by
    author:       Jane Doe <jane@example.org>
    tags:         salton-sea, water
    extras:       uploadType=dataset, lastUpdateDate=2026-11-19,
                  pocName=Jane Doe, pocEmail=jane@example.org,
                  pelican_expected_count=1,
                  pelican_ingest_status=ready, pelican_ingest_errors=[]
    resources:
      salton/readings.csv  pelican://osg-htc.org/example-ns/salton/readings.csv
                           text/csv, status active, size 1024, hash 0979c8a0...

The first event creates the package and brings it to its expected count in the same step, so validation runs right away and the dataset is ready for curator review.

6.2 Several files, same metadata on each

The simplest pattern for a multi-file dataset: attach the identical record to every file. That is what pelican object put -r --metadata-file record.json does. Order doesn't matter and losing any one file loses nothing but that file. The three events may arrive at once; they are applied one at a time (3.4), and the resources are listed in path order whatever order they arrive in.

    /example-ns/station7/2026-01.csv
    /example-ns/station7/2026-02.csv
    /example-ns/station7/2026-03.csv
      each with:
      dataset=station7-monthly
      expected_count=3
      title=Station 7 Monthly
      notes=Monthly exports from station 7.
      tags=seismic,station-7
      license_id=cc-by
      uploadType=dataset
      lastUpdateDate=2026-11-19
      pocName=Jane Doe
      pocEmail=jane@example.org

What happens:

- file 1: package example-org-station7-monthly created with all fields and one resource. 1 of 3, no validation yet.
- file 2: same fields re-applied (no change), second resource. 2 of 3.
- file 3: third resource. 3 of 3, validation runs and passes.

In CKAN:

    title:        Station 7 Monthly
    license_id:   cc-by
    author:       (none - not sent; it is optional)
    tags:         seismic, station-7
    extras:       uploadType=dataset, lastUpdateDate=2026-11-19,
                  pocName=Jane Doe, pocEmail=jane@example.org,
                  pelican_expected_count=3, pelican_ingest_status=ready
    resources:
      station7/2026-01.csv  pelican://osg-htc.org/example-ns/station7/2026-01.csv  text/csv
      station7/2026-02.csv  pelican://osg-htc.org/example-ns/station7/2026-02.csv  text/csv
      station7/2026-03.csv  pelican://osg-htc.org/example-ns/station7/2026-03.csv  text/csv

6.3 Several files, metadata spread across them

Each file only carries dataset and expected_count plus whatever part of the record the uploader has at hand. The receiver merges every event's fields into the package; when the same key comes twice, the later event wins. Validation waits until the last file is in, so the earlier, partial states are fine.

    /example-ns/snow/README.md
      dataset=snow-survey
      expected_count=3
      title=Snow Survey (draft title)
      notes=Snow depth survey, winter 2026.

    /example-ns/snow/depths.csv
      dataset=snow-survey
      expected_count=3
      title=Snow Survey 2026
      tags=snow,hydrology
      uploadType=dataset
      author=Sam Lee

    /example-ns/snow/sites.geojson
      dataset=snow-survey
      expected_count=3
      pocName=Sam Lee
      pocEmail=sam@example.org
      lastUpdateDate=2026-11-19
      license_id=odc-by
      owner_org=someone-else

The package after each event:

    after README.md      title=Snow Survey (draft title), notes
                         1 of 3 resources, not validated
    after depths.csv     title=Snow Survey 2026 (later value wins), notes,
                         tags, uploadType, author
                         2 of 3, not validated
    after sites.geojson  + pocName, pocEmail, lastUpdateDate, license_id;
                         owner_org dropped and logged
                         3 of 3, validation runs and passes

In CKAN:

    title:        Snow Survey 2026
    notes:        Snow depth survey, winter 2026.
    organization: example-org (not someone-else)
    license_id:   odc-by
    author:       Sam Lee
    tags:         hydrology, snow
    extras:       uploadType=dataset, lastUpdateDate=2026-11-19,
                  pocName=Sam Lee, pocEmail=sam@example.org,
                  pelican_expected_count=3, pelican_ingest_status=ready
    resources:
      snow/README.md      pelican://osg-htc.org/example-ns/snow/README.md      text/markdown
      snow/depths.csv     pelican://osg-htc.org/example-ns/snow/depths.csv     text/csv
      snow/sites.geojson  pelican://osg-htc.org/example-ns/snow/sites.geojson  application/geo+json

"Later wins" means later to arrive at the receiver, not later in upload order. Pelican does not guarantee delivery order, so if two files carry different values for the same key, either could end up in CKAN. Send a key once, or send the same value every time.

The same pattern with a required field never sent. Two files, expected_count=2; between them they carry title, notes, tags, uploadType, pocName and lastUpdateDate but no pocEmail. When the second file lands, validation runs and fails, and the package sits as an incomplete draft:

    pelican_ingest_status:  incomplete
    pelican_ingest_errors:  ["Missing required field for public dataset: extras:pocEmail"]

Validation re-runs on every event once the count is reached, so the uploader can fix this without help: upload any file of the dataset again with pocEmail attached. Re-uploading an existing path updates that resource rather than adding one, so the count stays at 2, and the package turns ready (checked on the bench). Otherwise a sysadmin fixes the draft in Prekan by hand, or a reviewer rejects it (3.4). Either way this only works before review; after approval or rejection the event gets a 422.

6.4 A logical unit plus a file listed on its own

A shapefile is four files that are only useful together, so they share one resource. The README is listed separately so readers find it. expected_count is 2 because the dataset has two resources, not five files. In practice this is two uploads: a put -r of the roads folder with one metadata file, then a put of the README with another.

    /example-ns/gis/roads/roads.shp
    /example-ns/gis/roads/roads.shx
    /example-ns/gis/roads/roads.dbf
    /example-ns/gis/roads/roads.prj
      each with:
      dataset=county-roads
      expected_count=2
      resource=/example-ns/gis/roads
      format=shapefile
      resource_description=Road centrelines, one shapefile
      title=County Roads
      notes=Road network for the county.
      tags=roads,gis
      license_id=cc-by
      uploadType=dataset
      lastUpdateDate=2026-11-21
      pocName=Jane Doe
      pocEmail=jane@example.org

    /example-ns/gis/README.md
      the same dataset fields, without resource and format, and
      resource_description=Start here: sources and field definitions

What happens:

- the four shapefile parts (sent at once, as put -r would): the first creates the package and the roads resource; the other three update that same resource. 1 of 2 resources.
- README.md: second resource, inserted before gis/roads in path order. 2 of 2, validation runs and passes.

In CKAN:

    title:        County Roads
    license_id:   cc-by
    tags:         gis, roads
    extras:       uploadType=dataset, lastUpdateDate=2026-11-21,
                  pocName=Jane Doe, pocEmail=jane@example.org,
                  pelican_expected_count=2, pelican_ingest_status=ready
    resources:
      gis/README.md  pelican://osg-htc.org/example-ns/gis/README.md
                     text/markdown, "Start here: sources and field definitions"
      gis/roads      pelican://osg-htc.org/example-ns/gis/roads/
                     format SHP, mimetype inode/directory, no size,
                     "Road centrelines, one shapefile",
                     last_modified = newest of the four files

A unit counts toward expected_count as soon as its first file lands, so a dataset can turn ready before every file of a unit is in. Curator review is the backstop for that.

References

- Pelican metadata-publish-design.md, commit 6fac86f061a0c816d07fb4d41e7bebc3fbcf6897
- Pelican docs/scopes.yaml and docs/user-group-design.md, same commit
- NDP CKAN extension Design Doc (Kai Lin), existing Google Doc
- "Pelican/NDP integration" email thread, 2026-10-14 to 2026-10-26 (Kai, Ismael), including Kai's pelican_resources.xlsx listing the Pelican resources already in the NDP catalog
- ndp-test instances: Prekan at https://ndp-test.sdsc.edu/catalog2 (API docs at /catalog2/ndp/api-docs), production-style CKAN at https://ndp-test.sdsc.edu/catalog
