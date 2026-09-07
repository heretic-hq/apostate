# Subsystem 11 — Storage, Quota & Off-the-Record Contexts

Whether a session keeps its cookies and storage, and why that choice is a
fingerprinting decision rather than a convenience one.

Entry points marked **verified** were confirmed against the pinned checkout of
152.0.7977.82, not codesearch, which indexes `main`.

## The finding that makes this a subsystem

**A non-persistent automation context is not *like* incognito. It is
incognito.** Same profile type, same code path, same storage behaviour.

Playwright's `browser.newContext()` and Puppeteer's `createBrowserContext()`
both issue CDP `Target.createBrowserContext`. That reaches
`content/browser/devtools/protocol/target_handler.cc:1574` — **verified** —
which calls the embedder's delegate, and in Chrome that is
`chrome/browser/devtools/devtools_browser_context_manager.cc:56` —
**verified**:

```c++
Profile* otr_profile = original_profile->GetOffTheRecordProfile(
    Profile::OTRProfileID::CreateUniqueForDevTools(),
    /*create_if_needed=*/true);
```

`content/browser/storage_partition_impl.cc:3496` — **verified** — then passes
`browser_context_->IsOffTheRecord()` into the quota manager as `is_incognito`.

Only a real `--user-data-dir` (Playwright's `launchPersistentContext`) avoids
this. The default does not, which means the default configuration of the two
most widely used automation stacks inherits every off-the-record difference
without the operator choosing it.

## What that cost us, before patch 0022

Incognito storage quota is computed from **physical memory**, not disk.
`storage/browser/quota/quota_settings.cc:35` — **verified**:

```c++
double incognito_pool_size_ratio =
    0.15 + (base::RandDouble() * (0.2 - 0.15));
settings.pool_size = physical_memory_amount * incognito_pool_size_ratio;
```

`physical_memory_amount` came from `base::SysInfo::AmountOfTotalPhysicalMemory()`
— the host's real installed RAM. `navigator.deviceMemory` does not cover this
and cannot: patch 0006 patches `ApproximatedDeviceMemory`, a *consumer* of the
memory fact rather than the fact itself, and deviceMemory saturates anyway —
measured on the build host it reports 32 both for a 32 GiB claim and for the
real 93.4 GiB machine.

Patch 0022 supplies the profile's memory at
`QuotaDeviceInfoHelper::AmountOfPhysicalMemory()`, which the incognito branch is
the only caller of.

## The 10 GiB cap, which hides the leak on large hosts

`ContinueIncognitoGetStorageCapacity` (`quota_manager_impl.cc:2695` —
**verified**) passes `settings.pool_size` as `total_space`, and
`CalculateReportedQuota` then applies the same cap it applies in regular mode:
at or above 10 GiB it returns `usage + 10 GiB`, below it returns the pool
rounded up to the nearest GiB.

So the incognito quota only differs from the regular one while the pool stays
under 10 GiB:

| host RAM | pool at ratio 0.15–0.2 | incognito quota |
|---|---|---|
| ≤ 50 GiB | under 10 GiB | pool rounded up — distinguishable |
| 50–66.7 GiB | straddles 10 GiB | depends on the session's draw |
| > 66.7 GiB | over 10 GiB | exactly 10 GiB — same as regular |

This is why the leak was easy to miss. On our 93.4 GiB build host an unpatched
incognito session reported exactly 10 GiB, which looks unremarkable. It was
wrong not as a number but as a *machine*: it described a host with at least
50 GiB of RAM while the profile claimed 32.

## Measured, after the patch

Same binary, same host, `--incognito` throughout:

```
no profile        quota 10737418240    host pool hits the cap
32 GiB profile    quota  7516192768    session A
32 GiB profile    quota  6442450944    session B
regular mode      quota 10737418240    unchanged
```

Sessions A and B differ because the ratio is redrawn per session, and both sit
inside the range a real 32 GiB machine produces. That variation is kept
deliberately — see below.

A full collector capture in incognito against one in regular mode conforms
**30/30** on non-volatile probes. `storage.estimate` is in `ALWAYS_VOLATILE`, so
that result excludes the quota and is not evidence about it; the quota numbers
above are the evidence.

## Why the randomness stays

`base::RandDouble()` above looks like an axiom A3 violation. It is not. It is
drawn once per `QuotaManagerImpl`, the incognito `refresh_interval` is
`base::TimeDelta::Max()`, and `QuotaManagerImpl::DidGetSettings` caches the
result — so the value is fixed for the life of the session and varies between
sessions, which is what every real Chrome does.

Deriving it from the profile would make one identity report a single constant
quota forever where real browsers vary. That replaces a fixed tell with a new
one, which is the failure A2 exists to prevent.

## Guidance

**Default to a persistent profile.** Launch with a real `--user-data-dir`, or
`launchPersistentContext` if driving through Playwright. Cookies and
localStorage then survive across sessions, which is both what an operator
usually wants and what a returning human looks like.

**Incognito is supported, not forbidden.** Real people browse in incognito, and
a browser that cannot is itself unusual. After patch 0022 an off-the-record
session describes the claimed device rather than the host. A page can still tell
it is off-the-record — so can it for a real user — but it can no longer tell
what machine is underneath.

**Do not try to make incognito look like regular mode.** The target is not
"indistinguishable from a persistent session"; it is "equal to what reference
device D reports in incognito". Those are different, and only the second is
coherent.

## Residuals

- Storage does not survive an off-the-record session, by construction. That is
  the behaviour, not a defect, and it is the reason to prefer persistent.
- We have not enumerated every off-the-record difference beyond what the 36
  collector probes reach. The 30/30 above bounds what those probes can see and
  claims nothing outside it.
