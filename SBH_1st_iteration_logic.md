# SBH First Iteration – Code Logic

## Goal

This code implements the **first bottleneck-selection step** of the Shifting Bottleneck Heuristic (SBH).

For each machine, it solves:

`1 | r_j | L_max`

using exact branch-and-bound, then selects the machine with the **largest optimal L_max** as the first bottleneck.

---

## 1. Input to Each Machine Subproblem

Each operation is represented by:

`(r_j, p_j, d_j)`

where:

- `p_j` = processing time from the LEKIN input
- `d_j` = original job due date from the LEKIN input
- `r_j` = earliest time the operation can start

Release time is computed as:

`r_j = max(job ready time, machine release time)`

The job ready time starts from the job release time and is updated along the job route using the earliest possible completion of the previous operation.

No artificial SBH due dates are used in this implementation.

---



## 2. Single-Machine Objective

For each machine, the objective is:

`min L_max`

where:

`L_j = C_j - d_j`

`L_max = max_j(L_j)`

For a given sequence:

`S_j = max(current time, r_j)`

`C_j = S_j + p_j`

`L_j = C_j - d_j`

---



## 3. Initial Upper Bound

An EDD sequence is first generated:

`d_j ↑`

Its `L_max` becomes the initial upper bound:

`UB = L_max of the EDD sequence`

EDD is also used to decide which branches are explored first.

It is only a search priority rule here; the final sequence is still determined by branch-and-bound.

---



## 4. Lower Bounds

At each B&B node, three lower bounds are computed.

### LB1 — Current prefix

`LB1 = current prefix L_max`

The lateness already created by the current prefix cannot decrease.

### LB2 — Earliest individual completion

`LB2 = max_j { max(t, r_j) + p_j - d_j }`

Each remaining operation is considered independently.

This captures lateness that is already unavoidable because of its release time.

### LB3 — Single-machine sequencing effect

Release dates are ignored temporarily, giving the relaxed problem:

`1 || L_max`

The remaining operations are ordered by EDD and their processing times are accumulated.

This captures the sequencing / machine-load effect that LB2 ignores.

### Final lower bound

`LB = max(LB1, LB2, LB3)`

---



## 5. Pruning

The search prunes in two places.

### Node pruning

`if LB >= UB -> prune`

If the best possible result from the current node cannot improve the current best solution, the entire node is discarded.

### Immediate branch pruning

After adding the next operation:

`if next L_max >= UB -> prune`

This is valid because `L_max` cannot decrease as more operations are added.

Whenever a better complete sequence is found:

`UB = new L_max`

---



## 6. Bottleneck Selection

After solving every machine subproblem, the code obtains:

`optimal L_max for each machine`

The first bottleneck is the machine with the **largest optimal L_max**.

So the logic is:

- within each machine: **minimize L_max**
- across machines: **select the largest optimal L_max**

If there is a tie, the smaller workcenter name is selected for deterministic output.

---



## 7. LEKIN Visualization

The first SBH iteration identifies the bottleneck and its optimal machine sequence, but it does not yet produce a complete job-shop schedule.

LEKIN requires a complete feasible `Schedule` object for display, so the code builds a separate visualization schedule.

This display schedule is **not the B&B / SBH result**.

---



## Overall Flow

```text
LEKIN jobs / machines
        ↓
Build (r_j, p_j, d_j)
        ↓
For each machine:
    EDD sequence
        ↓
    Initial UB
        ↓
    Branch-and-Bound
      ├─ compute LB1, LB2, LB3
      ├─ prune if LB >= UB
      ├─ branch in EDD order
      └─ update UB if a better sequence is found
        ↓
Optimal L_max for each machine
        ↓
Select the largest optimal L_max
        ↓
FIRST BOTTLENECK
```

