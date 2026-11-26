# Native screen batch selection checkpoint

Patch the exact 233 screen source pin 4edbd50feb36e47d072b5cd4e516b9792b8b4ea9
with patch_batch_screen.py in a separate staging tree. The source changes
add an explicit Multi-select toggle to the inventory, preserve selections
across ACE tabs, display their count, retain exact zero-based bay addresses,
check the connection generation and unit serial, and reuse the confirmation
page. Loading two spools into the same head is rejected. Eject selection can
contain all sixteen slots. No fallback to a repeated single-slot G-code.

Intended provider/companion contract (not enabled):
- POST /material/batch/preview {action,slots:[{ace,slot,serial},...]}: read
  actual routing and identity, validate selection, return a short-lived,
  single-use opaque ticket bound to the exact live unit/connection and route.
  No movement. Never trust a client-supplied parked flag.
- POST /material/batch/start {ticket}: recheck identity, idle/route, consume
  ticket exactly once, enqueue through the one shared host coordinator;
  return {batch:id} as acceptance only, not completion.
- Per-slot status and exact task cancellation must come from the coordinator,
  not a global busy flag. Unknown or failed route never returns success.

This commit tests the selection model in C++17 and applies the source patch
against its pinned inputs. It does NOT compile the complete ARM screen binary
or install the batch provider endpoints. Do not package this source patch
before those steps. Existing single-slot behavior is unchanged outside
multi-select; no movement occurs on a missing/failed preview endpoint.
