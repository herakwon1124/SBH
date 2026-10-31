"""First iteration of the Shifting Bottleneck Heuristic (SBH).

1. Read the LEKIN job and machine data.
2. Build (r_j, p_j, d_j) for each operation.
3. Solve 1 | r_j | L_max for each machine using exact branch-and-bound.
4. Select the machine with the largest optimal L_max as the bottleneck.
5. Return a separate feasible schedule for LEKIN visualization.

Due dates d_j are taken directly from the input job due dates.
No artificial SBH due dates are computed.
"""

from dataclasses import dataclass

from lekinpy import (
    MachineSchedule,
    Schedule,
    ScheduledOperation,
)


# ============================================================
# Data used by a machine subproblem
# ============================================================

@dataclass(frozen=True)
class OperationData:
    """One operation on one machine, with data for 1 | r_j | L_max.

    r : release time r_j  (earliest start)
    p : processing time p_j
    d : due date d_j
    """

    job: object
    job_id: str
    operation_index: int
    operation: object
    workcenter_name: str
    r: float
    p: float
    d: float

    @property
    def name(self):
        return f"{self.job_id}-O{self.operation_index}"


# ============================================================
# 1. Read LEKIN machine information
# ============================================================

def build_machine_map(system):
    """Map each workcenter name to its machine and release time.

    Assumes one workcenter = one machine.

    Returns:
        machine_by_workcenter: workcenter name -> machine object
        machine_release_time: workcenter name -> earliest usable time
    """
    machine_by_workcenter = {}
    machine_release_time = {}

    for workcenter in system.workcenters:
        machines = list(workcenter.machines)

        if len(machines) != 1:
            raise ValueError(
                f"{workcenter.name} must contain exactly one machine."
            )

        machine = machines[0]
        machine_by_workcenter[workcenter.name] = machine

        #Resource release time = max(workcenter release, machine release)
        machine_release_time[workcenter.name] = max(
            0,
            getattr(workcenter, "release", 0),
            getattr(machine, "release", 0),
        )

    return machine_by_workcenter, machine_release_time


# ============================================================
# 2. Convert LEKIN input into (r_j, p_j, d_j)
# ============================================================

def build_machine_subproblems(system, machine_release_time):
    """Build each machine's operation list for 1 | r_j | L_max

    For operation j of a job:
        p_j = processing time from the input
        d_j = the job's input due date (not an artificial SBH due date!!!)
        r_j = max(job ready time, machine release time)

    Job ready time starts at the job release and is updated to the earliest possible completion of the previous operation (r_prev + p_prev).
    """
    operations_by_workcenter = {
        workcenter.name: []
        for workcenter in system.workcenters
    }

    for job in system.jobs:
        job_release = getattr(job, "release", 0)
        job_due = getattr(job, "due", 0)
        job_ready_time = job_release

        for operation_index, operation in enumerate(job.operations):
            workcenter_name = operation.workcenter
            p = operation.processing_time
            r = max(job_ready_time, machine_release_time[workcenter_name])
            d = job_due

            operation_data = OperationData(
                job=job,
                job_id=job.job_id,
                operation_index=operation_index,
                operation=operation,
                workcenter_name=workcenter_name,
                r=r,
                p=p,
                d=d,
            )
            operations_by_workcenter[workcenter_name].append(operation_data)

            #Update the job_ready_time(Next operation of this job cannot start before this one ends.)
            job_ready_time = r + p

    return operations_by_workcenter


# ============================================================
# 3. Exact B&B for one machine: 1 | r_j | L_max
# ============================================================

def _edd_priority_key(operations, index):
    """EDD: sort by d_j

    purpose: initial UB + branch order
    not optimal in general for 1 | r_j | L_max
    """
    operation = operations[index]
    return (
        operation.d,
        operation.r,
        operation.p,
        operation.job_id,
        operation.operation_index,
    )

def _operation_names(operations, order):
    #just for display
    return [operations[index].name for index in order]

def _evaluate_sequence(operations, order):
    """Schedule a complete sequence on one machine (no overlap).

    For each operation in order:
        S_j = max(machine ready time, r_j)
        C_j = S_j + p_j
        L_j = C_j - d_j

    Returns:
        L_max of the sequence, and a record for each operation.
    """
    machine_ready_time = 0
    lmax = float("-inf")
    records = []

    for index in order:
        operation = operations[index]
        start_time = max(machine_ready_time, operation.r)
        completion_time = start_time + operation.p
        lateness = completion_time - operation.d
        lmax = max(lmax, lateness)

        records.append({
            "operation": operation,
            "start_time": start_time,
            "completion_time": completion_time,
            "lateness": lateness,
        })
        machine_ready_time = completion_time

    return lmax, records


def _compute_lower_bound(operations, remaining, current_time, current_lmax):
    """Lower bound(LB) on L_max for a partial sequence (B&B node).

    LB1 = current prefix L_max

    LB2 = max_j {max(t, r_j) + p_j - d_j}
      Operations are considered independently.
      -> captures unavoidable lateness from r_j

    LB3 = relaxed 1 || L_max (EDD order)
      Processing times are accumulated, but r_j is ignored.
      -> captures single-machine sequencing effect

    Returns LB = max(LB1, LB2, LB3)
    """
    lb1 = current_lmax

    lb2 = current_lmax
    for index in remaining:
        operation = operations[index]
        earliest_completion = max(current_time, operation.r) + operation.p
        lb2 = max(lb2, earliest_completion - operation.d)

    # Relaxed 1 || L_max: sequence remaining jobs by due date, ignore r_j.
    relaxed_time = current_time
    lb3 = current_lmax
    edd_order = sorted(
        remaining,
        key=lambda index: (
            operations[index].d,
            operations[index].job_id,
            operations[index].operation_index,
        ),
    )
    for index in edd_order:
        operation = operations[index]
        relaxed_time += operation.p
        lb3 = max(lb3, relaxed_time - operation.d)

    lb = max(lb1, lb2, lb3)
    return {"lb": lb, "lb1": lb1, "lb2": lb2, "lb3": lb3}


def solve_lmax_subproblem(operations, context=None, trace=False):
    """Exact B&B for 1 | r_j | L_max.

    Objective: min L_max = max_j(C_j - d_j)
    EDD -> initial UB + branch order

    Prune if LB >= UB
    """
    n = len(operations)

    if n == 0:
        return {
            "sequence": (),
            "lmax": float("-inf"),
            "records": [],
            "nodes_explored": 0,
            "nodes_pruned": 0,
            "stopped": False,
        }

    # EDD sequence for initial UB
    best_order = tuple(sorted(range(n), key=lambda i: _edd_priority_key(operations, i)))
    best_lmax, best_records = _evaluate_sequence(operations, best_order)

    nodes_explored = 0
    nodes_pruned = 0
    stopped = False

    if trace:
        print()
        print("Initial incumbent (EDD-like)")
        print("  sequence =", _operation_names(operations, best_order))
        print("  UB       =", best_lmax)

    def branch_and_bound(prefix, remaining, current_time, current_lmax):
        
        nonlocal best_order, best_lmax, best_records
        nonlocal nodes_explored, nodes_pruned, stopped

        # Stop if requested by LEKIN
        if context is not None and hasattr(context, "should_stop") and context.should_stop():
            stopped = True
            return

        nodes_explored += 1

        if trace:
            print()
            print(f"[Node {nodes_explored}]")
            print("  prefix       =", _operation_names(operations, prefix))
            print("  remaining    =", _operation_names(operations, remaining))
            print("  current time =", current_time)
            print("  current Lmax =", current_lmax)
            print("  current UB   =", best_lmax)

        # Complete sequence: update UB if improved
        if not remaining:
            if current_lmax < best_lmax:
                best_order = prefix
                best_lmax = current_lmax
                _, best_records = _evaluate_sequence(operations, best_order)

                if trace:
                    print("  -> new incumbent")
                    print("     sequence =", _operation_names(operations, prefix))
                    print("     UB       =", best_lmax)
            return

        # LB = max(LB1, LB2, LB3)
        bounds = _compute_lower_bound(
            operations, remaining, current_time, current_lmax
        )
        lb = bounds["lb"]

        if trace:
            print("  LB1 prefix              =", bounds["lb1"])
            print("  LB2 earliest completion =", bounds["lb2"])
            print("  LB3 EDD relaxation      =", bounds["lb3"])
            print("  final LB                =", lb)

        # Prune if LB >= UB.
        if lb >= best_lmax:
            nodes_pruned += 1
            if trace:
                print(f"  -> prune by bound ({lb} >= {best_lmax})")
            return

        # Branch in EDD order to find a good UB early.
        candidates = sorted(
            remaining,
            key=lambda i: _edd_priority_key(operations, i),
        )

        for index in candidates:
            # S_j = max(t, r_j)
            # C_j = S_j + p_j
            # L_j = C_j - d_j
            operation = operations[index]
            start_time = max(current_time, operation.r)
            completion_time = start_time + operation.p
            lateness = completion_time - operation.d
            next_lmax = max(current_lmax, lateness)

            if trace:
                print()
                print(f"  try {operation.name}")
                print(f"    r = {operation.r}")
                print(f"    p = {operation.p}")
                print(f"    d = {operation.d}")
                print(f"    S = {start_time}")
                print(f"    C = {completion_time}")
                print(f"    L = {lateness}")
                print(f"    new Lmax = {next_lmax}")

            # L_max cannot decrease -> prune if next L_max >= UB.
            if next_lmax >= best_lmax:
                nodes_pruned += 1
                if trace:
                    print(f"    -> prune immediately ({next_lmax} >= {best_lmax})")
                continue

            next_remaining = tuple(i for i in remaining if i != index)
            branch_and_bound(
                prefix + (index,),
                next_remaining,
                completion_time,
                next_lmax,
            )
            if stopped:
                return
    # Root node:
    # prefix = empty, remaining = all operations
    branch_and_bound(
        prefix=(),
        remaining=tuple(range(n)),
        current_time=0,
        current_lmax=float("-inf"),
    )

    # Final optimal sequence
    best_lmax, best_records = _evaluate_sequence(operations, best_order)
    optimal_sequence = tuple(operations[index] for index in best_order)

    return {
        "sequence": optimal_sequence,
        "lmax": best_lmax,
        "records": best_records,
        "nodes_explored": nodes_explored,
        "nodes_pruned": nodes_pruned,
        "stopped": stopped,
    }


# ============================================================
# 4. Solve every machine and choose the first bottleneck
# ============================================================

def solve_first_iteration(operations_by_workcenter, context=None, trace=False):
    """Solve 1 | r_j | L_max on every machine, then pick the bottleneck.

    Bottleneck = machine with the largest optimal L_max.
    Ties are broken by smaller workcenter name.

    Returns:
        result_by_workcenter, bottleneck_workcenter
    """
    result_by_workcenter = {}

    for workcenter_name, operations in operations_by_workcenter.items():
        if not operations:
            continue

        if trace:
            print()
            print("#" * 50)
            print(f"SOLVING {workcenter_name}")
            print("#" * 50)

        result_by_workcenter[workcenter_name] = solve_lmax_subproblem(
            operations=operations,
            context=context,
            trace=trace,
        )

    if not result_by_workcenter:
        raise ValueError("No machine subproblems were created.")

    bottleneck_workcenter = min(
        result_by_workcenter,
        key=lambda name: (-result_by_workcenter[name]["lmax"], name),
    )

    return result_by_workcenter, bottleneck_workcenter


# ============================================================
# 5. Print first-iteration result
# ============================================================

def print_first_iteration_summary(
    operations_by_workcenter,
    result_by_workcenter,
    bottleneck_workcenter,
    machine_by_workcenter,
):
    # Print each machine's subproblem data and the chosen bottleneck
    print()
    print("=" * 60)
    print("FIRST ITERATION SUMMARY")
    print("Machine subproblem: 1 | r_j | L_max")
    print("=" * 60)

    for workcenter_name in sorted(result_by_workcenter):
        print()
        print(f"--- {workcenter_name} ---")
        print("Subproblem:")

        for operation in operations_by_workcenter[workcenter_name]:
            print(
                f"  {operation.name}: "
                f"r={operation.r}, "
                f"p={operation.p}, "
                f"d={operation.d}"
            )

        result = result_by_workcenter[workcenter_name]
        optimal_sequence = [operation.name for operation in result["sequence"]]

        print("Optimal sequence =", optimal_sequence)
        print("Optimal Lmax =", result["lmax"])
        print("Nodes explored =", result["nodes_explored"])
        print("Nodes pruned =", result["nodes_pruned"])
        print("Optimality proven =", not result["stopped"])

    bottleneck_result = result_by_workcenter[bottleneck_workcenter]
    bottleneck_sequence = [
        operation.name for operation in bottleneck_result["sequence"]
    ]

    print()
    print("=" * 60)
    print("FIRST BOTTLENECK")
    print("Workcenter =", bottleneck_workcenter)
    print("Machine =", machine_by_workcenter[bottleneck_workcenter].name)
    print("Optimal Lmax =", bottleneck_result["lmax"])
    print("Sequence =", bottleneck_sequence)
    print("=" * 60)


# ============================================================
# 6. Feasible LEKIN schedule (just for visualization)
# ============================================================
#    LEKIN requires a complete feasible Schedule for display
#    Not the B&B / SBH result

def build_visualization_schedule(system, machine_by_workcenter, machine_release_time):
    """Returns a simple feasible schedule so LEKIN can display output.
    """
    machine_available_time = dict(machine_release_time)
    job_available_time = {
        job.job_id: getattr(job, "release", 0)
        for job in system.jobs
    }
    next_operation_index = {job.job_id: 0 for job in system.jobs}
    scheduled_operations_by_workcenter = {
        workcenter.name: []
        for workcenter in system.workcenters
    }

    jobs = list(system.jobs)
    total_operations = sum(len(job.operations) for job in jobs)
    number_scheduled = 0

    while number_scheduled < total_operations:
        candidates = []

        # Only the next unscheduled operation of each job is eligible.
        for job in jobs:
            operation_index = next_operation_index[job.job_id]
            if operation_index >= len(job.operations):
                continue

            operation = job.operations[operation_index]
            workcenter_name = operation.workcenter
            earliest_start_time = max(
                job_available_time[job.job_id],
                machine_available_time[workcenter_name],
            )
            candidates.append((
                earliest_start_time,
                str(job.job_id),
                operation_index,
                job,
                operation,
            ))

        if not candidates:
            raise ValueError("Could not construct a valid LEKIN schedule.")

        start_time, _, operation_index, job, operation = min(candidates)
        workcenter_name = operation.workcenter
        machine = machine_by_workcenter[workcenter_name]
        completion_time = start_time + operation.processing_time
        sequence_position = len(scheduled_operations_by_workcenter[workcenter_name])

        scheduled_operation = ScheduledOperation(
            job_id=job.job_id,
            operation_index=operation_index,
            workcenter=workcenter_name,
            machine=machine.name,
            start_time=start_time,
            end_time=completion_time,
            sequence_position=sequence_position,
            status=operation.status,
        )
        scheduled_operations_by_workcenter[workcenter_name].append(
            scheduled_operation
        )

        operation.start_time = start_time
        operation.end_time = completion_time
        if operation_index == 0:
            job.start_time = start_time
        job.end_time = completion_time

        job_available_time[job.job_id] = completion_time
        machine_available_time[workcenter_name] = completion_time
        next_operation_index[job.job_id] += 1
        number_scheduled += 1

    machine_schedules = []
    all_scheduled_operations = []

    for workcenter in system.workcenters:
        scheduled_operations = scheduled_operations_by_workcenter[workcenter.name]
        all_scheduled_operations.extend(scheduled_operations)
        machine_schedules.append(
            MachineSchedule(
                workcenter=workcenter.name,
                machine=machine_by_workcenter[workcenter.name].name,
                operations=scheduled_operations,
            )
        )

    makespan = max(
        (operation.end_time for operation in all_scheduled_operations),
        default=0,
    )

    return Schedule(
        schedule_type="First Iteration 1|r_j|Lmax B&B",
        time=makespan,
        machines=machine_schedules,
    )


# ============================================================
# LEKIN entry point
# ============================================================

def schedule(system, parameters, context):
    """Run the first machine-bottleneck iteration for LEKIN.

    1. Read workcenter / machine information.
    2. Build r_j, p_j, d_j
    3. Solve 1 | r_j | L_max for each machine
    4. Select max L_max as the bottleneck
    5. Return a separate schedule for LEKIN display

    The returned schedule is not the B&B result.
    """
    parameters = parameters or {}
    trace = bool(parameters.get("trace", False))

    if hasattr(system, "validate"):
        system.validate()

    machine_by_workcenter, machine_release_time = build_machine_map(system)

    operations_by_workcenter = build_machine_subproblems(
        system=system,
        machine_release_time=machine_release_time,
    )

    result_by_workcenter, bottleneck_workcenter = solve_first_iteration(
        operations_by_workcenter=operations_by_workcenter,
        context=context,
        trace=trace,
    )

    print_first_iteration_summary(
        operations_by_workcenter=operations_by_workcenter,
        result_by_workcenter=result_by_workcenter,
        bottleneck_workcenter=bottleneck_workcenter,
        machine_by_workcenter=machine_by_workcenter,
    )

    return build_visualization_schedule(
        system=system,
        machine_by_workcenter=machine_by_workcenter,
        machine_release_time=machine_release_time,
    )