using AVEVA_Nexus.Exceptions;
using System;
using System.Runtime.CompilerServices;
using System.Threading;

namespace AVEVA_Nexus.Integration
{
    // E3D/PML is not thread-safe: simultaneous requests crashed the whole E3D session (observed in testing).
    // Every call into AVEVA goes through this single process-wide gate, which also serialises callers.
    //
    // Two modes:
    //  - "direct" (headless/TTY session, or no interface context captured): the work runs on the calling
    //    thread under a lock, as it always did.
    //  - "ui-thread" (graphical E3D): the work runs on E3D's interface thread. HostConfig.Start() is called
    //    from PML on that thread and captures its SynchronizationContext. Running model writes from a
    //    background thread hung a graphical E3D (Windows reported "stopped interacting"): writing refreshes
    //    the explorer and the 3D view, which belong to the interface thread.
    //
    // No wait is unbounded (each one ends in a 503):
    //  - waiting for the request that holds the gate: BusyTimeoutMs ("busy");
    //  - ui-thread mode, work queued but not started (a modal dialog, a long command): UiTimeoutMs; the work
    //    is then skipped if it has not started yet;
    //  - ui-thread mode, work started but still running: StuckTimeoutMs. The gate is then marked "stuck"
    //    (operation + since) until that work ends, and other callers fail fast instead of queueing behind it.
    // State() reports all this for /api/Health.
    //
    // Pure logic: no Aveva.* references (it is compiled by the unit-test runner).
    public static class AvevaGate
    {
        const int DefaultUiTimeoutMs = 30000;
        const int DefaultBusyTimeoutMs = 20000;
        const int DefaultStuckTimeoutMs = 120000;   // busy + ui + stuck = 170 s, under the MCP client's 190 s timeout

        static readonly object Lock = new object();
        static SynchronizationContext _ui;
        static int _uiThreadId;

        public static int UiTimeoutMs = DefaultUiTimeoutMs;
        public static int BusyTimeoutMs = DefaultBusyTimeoutMs;
        public static int StuckTimeoutMs = DefaultStuckTimeoutMs;

        // Written only by the thread holding Lock; read by State() from any thread.
        static int _depth;
        static string _currentOp;
        static long _currentSinceTicks;
        static int _waiting;

        sealed class StuckInfo
        {
            public object Owner;
            public string Operation;
            public DateTime SinceUtc;
        }
        static StuckInfo _stuck;

        public static string Mode { get { return Volatile.Read(ref _ui) == null ? "direct" : "ui-thread"; } }

        // NEXUS_UI_DISPATCH: on unless it is exactly "0". A headless session has no SynchronizationContext, so
        // it stays "direct" whatever this says.
        public static bool UiDispatchEnabled(string variableValue)
        {
            return variableValue != "0";
        }

        // Which context (if any) HostConfig.Start() should capture, given NEXUS_UI_DISPATCH and the
        // SynchronizationContext of the thread PML called it on:
        //  "0"                       -> null (direct), always.
        //  "1" / unset / "auto" / *  -> the current context if it is a real message-loop context (a subclass,
        //                               e.g. WindowsFormsSynchronizationContext in graphical E3D), else null.
        // The base SynchronizationContext is never captured, even with "1": its Post runs work on a thread-pool
        // thread, not on an interface thread, so a nested gate call from that work would deadlock on Lock.
        // A headless/TTY session has no context (or that base one): it stays direct.
        public static SynchronizationContext SelectUiContext(string variableValue, SynchronizationContext current)
        {
            if ((variableValue ?? "").Trim() == "0" || current == null) return null;
            return current.GetType() == typeof(SynchronizationContext) ? null : current;
        }

        // Timeouts from the environment (milliseconds, > 0); anything else keeps the current value.
        // NEXUS_GATE_BUSY_MS, NEXUS_GATE_UI_MS, NEXUS_GATE_STUCK_MS.
        public static void Configure(Func<string, string> environment)
        {
            BusyTimeoutMs = Positive(environment("NEXUS_GATE_BUSY_MS"), BusyTimeoutMs);
            UiTimeoutMs = Positive(environment("NEXUS_GATE_UI_MS"), UiTimeoutMs);
            StuckTimeoutMs = Positive(environment("NEXUS_GATE_STUCK_MS"), StuckTimeoutMs);
        }

        static int Positive(string text, int fallback)
        {
            int value;
            return int.TryParse((text ?? "").Trim(), out value) && value > 0 ? value : fallback;
        }

        // Called from the interface thread (HostConfig.Start). Null leaves the gate in direct mode.
        // Does NOT take Lock: on the interface thread that could wait for a request that is itself waiting
        // for the interface thread. The thread id is published before the context, so a reader that sees the
        // new context also sees its thread.
        public static void CaptureUiContext(SynchronizationContext context)
        {
            Volatile.Write(ref _uiThreadId, context == null ? 0 : Thread.CurrentThread.ManagedThreadId);
            Volatile.Write(ref _ui, context);
        }

        public static void Reset()
        {
            Volatile.Write(ref _ui, null);
            Volatile.Write(ref _uiThreadId, 0);
            UiTimeoutMs = DefaultUiTimeoutMs;
            BusyTimeoutMs = DefaultBusyTimeoutMs;
            StuckTimeoutMs = DefaultStuckTimeoutMs;
            Volatile.Write(ref _stuck, null);
        }

        public static GateState State()
        {
            string op = Volatile.Read(ref _currentOp);
            long since = Interlocked.Read(ref _currentSinceTicks);
            StuckInfo stuck = Volatile.Read(ref _stuck);
            DateTime now = DateTime.UtcNow;
            return new GateState
            {
                Mode = Mode,
                Busy = op != null,
                Waiting = Volatile.Read(ref _waiting),
                CurrentOperation = op,
                CurrentOperationSeconds = op == null ? (double?)null : Seconds(now, new DateTime(since, DateTimeKind.Utc)),
                Stuck = stuck != null,
                StuckOperation = stuck == null ? null : stuck.Operation,
                StuckSinceUtc = stuck == null ? (DateTime?)null : stuck.SinceUtc,
                StuckSeconds = stuck == null ? (double?)null : Seconds(now, stuck.SinceUtc),
            };
        }

        static double Seconds(DateTime now, DateTime since)
        {
            return Math.Round(Math.Max(0, (now - since).TotalSeconds), 1);
        }

        // operation: a name for State() and the error messages; the calling method by default.
        public static T Run<T>(Func<T> work, [CallerMemberName] string operation = null)
        {
            SynchronizationContext ui = Volatile.Read(ref _ui);

            // Already on the interface thread (a provider method calling another, from work this gate
            // posted). The caller that posted it holds Lock while waiting, so taking it here would
            // deadlock; and everything runs on this one thread anyway, so nothing can overlap.
            if (ui != null && Thread.CurrentThread.ManagedThreadId == Volatile.Read(ref _uiThreadId))
            {
                return work();
            }

            if (ui != null)
            {
                ThrowIfStuck();
            }

            Enter(operation);
            try
            {
                // Checked again holding the lock: a holder marked stuck releases it while its work still runs on
                // the interface thread, and nothing else may reach E3D until that work ends.
                if (ui != null) ThrowIfStuck();
                // direct: on the calling thread, serialised by Lock (re-entrant for nested calls)
                return ui == null ? work() : RunOnUi(ui, work, operation);
            }
            finally
            {
                Exit();
            }
        }

        static void ThrowIfStuck()
        {
            StuckInfo stuck = Volatile.Read(ref _stuck);
            if (stuck != null)
            {
                throw new NexusException(
                    "E3D is still running " + stuck.Operation + " (for " + (int)Seconds(DateTime.UtcNow, stuck.SinceUtc) +
                    " s); the gateway refuses new requests until it ends.", 503);
            }
        }

        static void Enter(string operation)
        {
            Interlocked.Increment(ref _waiting);
            bool entered;
            try { entered = Monitor.TryEnter(Lock, BusyTimeoutMs); }
            finally { Interlocked.Decrement(ref _waiting); }

            if (!entered)
            {
                string op = Volatile.Read(ref _currentOp);
                throw new NexusException(
                    "E3D is busy" + (op == null ? "" : " with " + op) + ": the request waited " + (BusyTimeoutMs / 1000) +
                    " s for the previous one and was not executed. Try again.", 503);
            }

            if (++_depth == 1)
            {
                Interlocked.Exchange(ref _currentSinceTicks, DateTime.UtcNow.Ticks);
                Volatile.Write(ref _currentOp, string.IsNullOrEmpty(operation) ? "request" : operation);
            }
        }

        static void Exit()
        {
            if (--_depth == 0)
            {
                Volatile.Write(ref _currentOp, null);
            }
            Monitor.Exit(Lock);
        }

        // Called holding Lock: one request at a time reaches the interface thread.
        static T RunOnUi<T>(SynchronizationContext ui, Func<T> work, string operation)
        {
            T result = default(T);
            Exception error = null;
            int state = 0;   // 0 queued, 1 started, 2 abandoned (timed out before it started)
            var started = new ManualResetEventSlim(false);
            var done = new ManualResetEventSlim(false);
            var owner = new object();

            ui.Post(_ =>
            {
                if (Interlocked.CompareExchange(ref state, 1, 0) != 0) return;   // abandoned: skip
                started.Set();
                try { result = work(); }
                catch (Exception ex) { error = ex; }
                finally
                {
                    done.Set();
                    ClearStuck(owner);
                }
            }, null);

            if (!started.Wait(UiTimeoutMs) && Interlocked.CompareExchange(ref state, 2, 0) == 0)
            {
                throw new NexusException(
                    "E3D's interface thread did not respond in " + (UiTimeoutMs / 1000) +
                    " s (is a dialog open in E3D?). The request was not executed.", 503);
            }

            // It has started: wait for it rather than abandon a running write, but not forever.
            if (!done.Wait(StuckTimeoutMs))
            {
                var stuck = new StuckInfo { Owner = owner, Operation = operation ?? "request", SinceUtc = DateTime.UtcNow };
                Interlocked.Exchange(ref _stuck, stuck);   // full fence: the check below must see done after publishing
                if (done.IsSet) ClearStuck(owner);   // it ended just now
                throw new NexusException(
                    "E3D is still running " + stuck.Operation + " after " + (StuckTimeoutMs / 1000) +
                    " s. Its result is lost; the gateway refuses new requests until it ends.", 503);
            }

            if (error != null)
            {
                var nexus = error as NexusException;
                if (nexus != null) throw new NexusException(nexus.Message, nexus.StatusCode);
                throw new InvalidOperationException(error.Message, error);
            }
            return result;
        }

        static void ClearStuck(object owner)
        {
            StuckInfo stuck = Volatile.Read(ref _stuck);
            if (stuck != null && stuck.Owner == owner)
            {
                Interlocked.CompareExchange(ref _stuck, null, stuck);
            }
        }
    }

    // Snapshot of the gate for /api/Health: names of provider operations and timings, no request data.
    public class GateState
    {
        public string Mode { get; set; }
        public bool Busy { get; set; }
        public int Waiting { get; set; }
        public string CurrentOperation { get; set; }
        public double? CurrentOperationSeconds { get; set; }
        public bool Stuck { get; set; }
        public string StuckOperation { get; set; }
        public DateTime? StuckSinceUtc { get; set; }
        public double? StuckSeconds { get; set; }
    }
}
