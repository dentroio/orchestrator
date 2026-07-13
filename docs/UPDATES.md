# Lab Updates - February 12, 2026

## Summary of Recent Changes

### 1. Hostname Convention for User Devices

**New Standard:** User device hostnames follow the pattern: **first initial + lastname + "-ws"**

#### Examples:
- `alice.johnson` → **ajohnson-ws**
- `henry.brown` → **hbrown-ws**
- `tina.clark` → **tclark-ws**
- `victor.wilson` → **vwilson-ws**
- `jane.robinson` → **jrobinson-ws**

#### What Was Updated:

**`identities1.json`:**
- Updated `device_name` field for all 10 user identities
- IoT device names remain unchanged (badge-reader-01, camera-01, etc.)

**Example:**
```json
{
  "username": "alice.johnson",
  "display_name": "Alice Johnson",
  "device_name": "ajohnson-ws",  // Changed from "alice-laptop"
  "department": "Sales",
  "password": "C!sco#123",
  "mac": "dc:a6:32:4f:0b:7c",
  "ssid": "netlab_employee"
}
```

#### Why This Matters:

- **Consistency:** Standard naming convention across all lab devices
- **DHCP Registration:** Hostnames are sent to DHCP and registered in DNS
- **Clarion Visibility:** Hostnames appear in Clarion data for correlation
- **ISE Profiling:** ISE can use hostname patterns for device profiling

---

### 2. Phased Approach - Data Validation First

**Important Change:** Lab execution is now split into distinct phases based on Clarion development status.

#### Current Reality:

**Clarion's analytics engine (grouping, policy generation) is still under development.**

This means:
- Full validation with grouping/policy scoring cannot happen yet
- Initial lab runs will focus on **data ingestion and correlation**
- Full analytics validation comes later

#### New Phase Structure:

**Phase 0: Data Validation & Initial Testing (CURRENT PRIORITY)**

**Goal:** Verify Clarion's data pipeline works correctly

**What to Test:**
- ✅ ISE pxGrid data collection
- ✅ NetFlow/IPFIX flow capture
- ✅ DNS query logging
- ✅ DHCP assignment recording
- ✅ Identity correlation (MAC ↔ Username ↔ IP ↔ Hostname)
- ✅ Traffic attribution (flows linked to correct identities)
- ✅ Data enrichment (AD groups, hostnames, ISE groups)

**Success Criteria:**
- 100% of identity switches visible in Clarion
- No correlation gaps (all MACs have username, all IPs have identity)
- Hostnames correctly formatted (ajohnson-ws, hbrown-ws, etc.)
- Traffic flows attributed to correct identities
- Manual spot-checks confirm data accuracy

**How to Validate:**
- Manual inspection of Clarion data
- Query for specific identities and verify completeness
- Check correlation across data sources
- Verify no orphaned data (MACs without identity, IPs without flows, etc.)

**Phase 1-3: Traffic Generation & Baseline (CURRENT)**

Same as before:
- Setup infrastructure
- Run baseline isolated persona tests
- Run mixed load with identity rotation

**Phase 4: Full Validation (FUTURE - When Analytics Ready)**

**Goal:** Score Clarion's grouping and policy generation

**Metrics:**
- Correlation coverage >= 90%
- Grouping purity >= 85%
- Backend exclusivity >= 90%
- False merges <= 10%
- Policy usefulness >= 80%
- Anomaly detection >= 75%

**When:** Once Clarion's analytics features are complete

---

### 3. Documentation Updates

#### Updated Files:

**`HOW_IT_WORKS.md`:**
- Added Phase 0 explanation (data validation)
- Clarified current vs. future lab goals
- Added hostname convention examples
- Updated Phase 4 to note it's for future use
- Added warning icons (⚠️) for in-development features

**`LAB_MASTER_PLAN.md`:**
- Added "Current Phase: Data Validation & Enrichment" section at top
- Inserted Phase 0 with detailed data validation checklist
- Updated Phase 4 with "Future" status
- Added hostname convention documentation
- Updated ground truth examples with correct hostnames

**`identities1.json`:**
- Updated all 10 user identities with new hostname format:
  - alice.johnson → ajohnson-ws
  - henry.brown → hbrown-ws
  - tina.clark → tclark-ws
  - victor.wilson → vwilson-ws
  - charlie.williams → cwilliams-ws
  - frank.thompson → fthompson-ws
  - henry.martin → hmartin-ws
  - bob.robinson → brobinson-ws
  - charlie.walker → cwalker-ws
  - jane.robinson → jrobinson-ws

---

## What This Means for Lab Execution

### Immediate Focus (Phase 0):

**Do This First:**
1. Deploy infrastructure (Phase 1 setup)
2. Run traffic generation with identity rotation
3. **Validate data collection in Clarion:**
   - Check if all identity switches appear
   - Verify MAC-to-username correlation
   - Confirm hostnames are correct (ajohnson-ws format)
   - Ensure traffic flows are attributed correctly
4. Fix any data pipeline issues
5. Iterate until Phase 0 success criteria are met

**Don't Do Yet:**
- Don't expect Clarion to group devices automatically
- Don't try to score grouping accuracy (validation script won't work yet)
- Don't expect policy recommendations
- Don't run full Phase 4 validation

### Future Work (Phase 4):

**Once Clarion Analytics Are Ready:**
1. Re-run lab with same traffic patterns
2. Let Clarion attempt grouping
3. Run `validate_clarion_grouping.py` for scoring
4. Compare against ground truth
5. Iterate on Clarion algorithms based on results

---

## Quick Start for Current Phase

### Step 1: Deploy Lab
```bash
# Follow DEPLOYMENT_GUIDE.md
# Deploy all Pis, configure identities, set up backends
```

### Step 2: Run Initial Test
```bash
# On orchestrator
python3 lab_orchestrator.py --schedule daily --duration 4
```

### Step 3: Manual Data Validation

**Check in Clarion UI/API:**

1. **Identity Visibility:**
   - Search for "ajohnson-ws" → Should find Alice's identity
   - Verify MAC: dc:a6:32:4f:0b:7c
   - Verify username: alice.johnson
   - Verify AD groups: Sales-Employees, Business-Users

2. **Traffic Attribution:**
   - Find flows from ajohnson-ws (or alice.johnson)
   - Destinations should include: finance.netlab.net, thehub.netlab.net
   - Protocol: HTTP/HTTPS
   - Verify flows are linked to correct identity

3. **IoT Devices:**
   - Search for "badge-reader-01"
   - Verify MAC: 00:04:5A:aa:11:01
   - Verify ISE group: Lab-BadgeReader
   - Verify exclusive destination: 192.168.31.2:9001

4. **Correlation Completeness:**
   - Check for orphaned MACs (MAC without username)
   - Check for orphaned IPs (IP without identity)
   - Check for missing flows (identity without traffic)

### Step 4: Document Issues

Create a list of data quality issues:
- ❌ 5% of identity switches missing from Clarion
- ❌ Hostnames not showing up in DHCP logs
- ❌ NetFlow not attributing to identity correctly
- ✅ ISE pxGrid working perfectly
- etc.

### Step 5: Fix & Iterate

Fix issues one by one:
- Configure DHCP logging
- Tune NetFlow collector
- Adjust ISE pxGrid settings
- Re-run test

Repeat until Phase 0 success criteria met.

---

## Key Takeaways

1. **Hostname Convention:** All user devices use `<firstInitial><lastname>-ws` format
2. **Phased Approach:** Data validation (Phase 0) comes before analytics validation (Phase 4)
3. **Current Focus:** Prove Clarion can collect and correlate data correctly
4. **Future Focus:** Prove Clarion can group and suggest policies accurately
5. **Be Patient:** Don't expect full analytics until Clarion's grouping engine is complete

---

## Questions & Answers

**Q: Why the hostname convention change?**

A: Standardization for easier tracking, DHCP/DNS registration, and consistent profiling.

**Q: Can we still run the full validation script?**

A: The script exists but won't produce meaningful results until Clarion analytics are ready. Focus on manual data validation for now.

**Q: How long will Phase 0 take?**

A: Initial run: 1-2 days. Fixing issues: 1-3 iterations. Budget 1-2 weeks for solid data pipeline.

**Q: What if data validation fails?**

A: Iterate! Fix data collection issues, tune integrations, adjust configurations, and re-test. This is the whole point of Phase 0.

**Q: When can we do full validation?**

A: Once Clarion's grouping and policy generation features are production-ready. Development team will communicate when analytics are ready for testing.

---

**Last Updated:** February 12, 2026  
**Version:** 2.0  
**Status:** Data Validation Phase
