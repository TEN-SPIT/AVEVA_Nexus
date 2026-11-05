using System;
using System.Collections.Concurrent;
using System.Threading;
using System.Threading.Tasks;
using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Integration;

namespace Nexus.Tests
{
    public static class AvevaGateTests
    {
        // A stand-in for E3D's interface thread: one thread that runs posted work in order.
        sealed class FakeUiThread : SynchronizationContext, IDisposable
        {
            readonly BlockingCollection<Tuple<SendOrPostCallback, object>> _queue =
                new BlockingCollection<Tuple<SendOrPostCallback, object>>();
            public readonly Thread Thread;
            readonly ManualResetEventSlim _unfreeze = new ManualResetEventSlim(true);

            public FakeUiThread()
            {
                Thread = new Thread(Loop) { IsBackground = true, Name = "fake-ui" };
                Thread.Start();
            }

            void Loop()
            {
                SetSynchronizationContext(this);
                foreach (var item in _queue.GetConsumingEnumerable())
                {
                    _unfreeze.Wait();
                    item.Item1(item.Item2);
                }
            }

            public override void Post(SendOrPostCallback d, object state) { _queue.Add(Tuple.Create(d, state)); }
            public void Freeze() { _unfreeze.Reset(); }
            public void Thaw() { _unfreeze.Set(); }
            public void Dispose() { Thaw(); _queue.CompleteAdding(); }

            // Run a delegate on the fake interface thread and wait (how PML would call HostConfig.Start()).
            public void RunOnThread(Action act)
            {
                var done = new ManualResetEventSlim();
                Post(_ => { act(); done.Set(); }, null);
                done.Wait();
            }
        }

        public static void RunAll()
        {
            Program.Run("UiDispatch_is_on_by_default_and_off_only_with_exactly_0", () =>
            {
                Check.True(AvevaGate.UiDispatchEnabled(null), "unset");
                Check.True(AvevaGate.UiDispatchEnabled("1"), "1");
                Check.True(AvevaGate.UiDispatchEnabled(""), "empty");
                Check.True(!AvevaGate.UiDispatchEnabled("0"), "0");
            });

            Program.Run("Gate_without_a_ui_context_runs_work_on_the_calling_thread", () =>
            {
                AvevaGate.Reset();
                AvevaGate.CaptureUiContext(null);
                Check.Equal("direct", AvevaGate.Mode, "mode");
                int caller = Thread.CurrentThread.ManagedThreadId;
                Check.Equal(caller, AvevaGate.Run(() => Thread.CurrentThread.ManagedThreadId), "thread");
            });

            Program.Run("Gate_with_a_ui_context_runs_work_on_the_ui_thread_even_when_called_from_another", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    Check.Equal("ui-thread", AvevaGate.Mode, "mode");
                    int seen = AvevaGate.Run(() => Thread.CurrentThread.ManagedThreadId);
                    Check.Equal(ui.Thread.ManagedThreadId, seen, "work ran on the ui thread");
                    Check.True(seen != Thread.CurrentThread.ManagedThreadId, "not the caller thread");
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_nested_runs_on_the_ui_thread_do_not_deadlock", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    int value = AvevaGate.Run(() => AvevaGate.Run(() => 7) + 1);
                    Check.Equal(8, value, "nested value");
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_returns_exceptions_from_ui_thread_work_to_the_caller", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    var ex = Check.Throws<NexusException>(
                        () => AvevaGate.Run<int>(() => { throw new NexusException("boom", 404); }), "propagates");
                    if (ex != null) Check.Equal(404, ex.StatusCode, "status kept");
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_times_out_with_503_when_the_ui_thread_is_blocked_and_skips_the_late_work", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    AvevaGate.UiTimeoutMs = 300;
                    ui.Freeze();
                    bool ran = false;
                    var ex = Check.Throws<NexusException>(() => AvevaGate.Run(() => { ran = true; return 1; }), "timeout");
                    if (ex != null) Check.Equal(503, ex.StatusCode, "status");
                    ui.Thaw();
                    Thread.Sleep(300);   // the abandoned work must not run now, against a client that already gave up
                    Check.True(!ran, "late work was skipped");
                    // and the gate works again afterwards
                    AvevaGate.UiTimeoutMs = 5000;
                    Check.Equal(3, AvevaGate.Run(() => 3), "usable again");
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_still_serialises_concurrent_callers_in_ui_mode", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    int inside = 0, maxInside = 0;
                    var threads = new Thread[8];
                    for (int i = 0; i < threads.Length; i++)
                    {
                        threads[i] = new Thread(() => AvevaGate.Run(() =>
                        {
                            int now = Interlocked.Increment(ref inside);
                            if (now > maxInside) maxInside = now;
                            Thread.Sleep(5);
                            Interlocked.Decrement(ref inside);
                            return 0;
                        }));
                        threads[i].Start();
                    }
                    foreach (var t in threads) t.Join();
                    Check.Equal(1, maxInside, "never two at once");
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_serialises_concurrent_callers_in_direct_mode", () =>
            {
                AvevaGate.Reset();
                int inside = 0, maxInside = 0;
                var threads = new Thread[8];
                for (int i = 0; i < threads.Length; i++)
                {
                    threads[i] = new Thread(() => AvevaGate.Run(() =>
                    {
                        int now = Interlocked.Increment(ref inside);
                        if (now > maxInside) maxInside = now;
                        Thread.Sleep(5);
                        Interlocked.Decrement(ref inside);
                        return 0;
                    }));
                    threads[i].Start();
                }
                foreach (var t in threads) t.Join();
                Check.Equal(1, maxInside, "never two at once");
            });

            Program.Run("Gate_direct_mode_answers_503_busy_instead_of_waiting_forever_for_the_lock", () =>
            {
                AvevaGate.Reset();
                AvevaGate.BusyTimeoutMs = 200;
                var release = new ManualResetEventSlim(false);
                var entered = new ManualResetEventSlim(false);
                var holder = new Thread(() => AvevaGate.Run(() => { entered.Set(); release.Wait(5000); return 0; }));
                holder.Start();
                entered.Wait(5000);
                var state = AvevaGate.State();
                Check.True(state.Busy, "busy while the holder runs");
                Check.True(state.CurrentOperation != null, "current operation named");
                var ex = Check.Throws<NexusException>(() => AvevaGate.Run(() => 1), "busy");
                if (ex != null)
                {
                    Check.Equal(503, ex.StatusCode, "status");
                    Check.True(ex.Message.Contains("busy"), "message says busy: " + ex.Message);
                }
                release.Set();
                holder.Join();
                Check.True(!AvevaGate.State().Busy, "not busy afterwards");
                Check.Equal(2, AvevaGate.Run(() => 2), "usable again");
                AvevaGate.Reset();
            });

            Program.Run("Gate_direct_mode_nested_calls_keep_the_gate_busy_until_the_outer_one_ends", () =>
            {
                AvevaGate.Reset();
                bool busyAfterInner = false;
                AvevaGate.Run(() => { AvevaGate.Run(() => 1); busyAfterInner = AvevaGate.State().Busy; return 0; });
                Check.True(busyAfterInner, "still busy inside the outer call");
                Check.True(!AvevaGate.State().Busy, "idle after");
            });

            Program.Run("Gate_ui_mode_answers_503_busy_when_another_request_holds_the_gate", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    AvevaGate.BusyTimeoutMs = 200;
                    var release = new ManualResetEventSlim(false);
                    var entered = new ManualResetEventSlim(false);
                    var holder = new Thread(() => AvevaGate.Run(() => { entered.Set(); release.Wait(5000); return 0; }));
                    holder.Start();
                    entered.Wait(5000);
                    var ex = Check.Throws<NexusException>(() => AvevaGate.Run(() => 1), "busy");
                    if (ex != null) Check.Equal(503, ex.StatusCode, "status");
                    release.Set();
                    holder.Join();
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_ui_mode_marks_itself_stuck_when_started_work_runs_too_long_and_recovers", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    AvevaGate.StuckTimeoutMs = 300;
                    var release = new ManualResetEventSlim(false);
                    var ex = Check.Throws<NexusException>(
                        () => AvevaGate.Run(() => { release.Wait(5000); return 0; }, "LongWrite"), "stuck");
                    if (ex != null) Check.Equal(503, ex.StatusCode, "status");

                    var state = AvevaGate.State();
                    Check.True(state.Stuck, "stuck flag");
                    Check.Equal("LongWrite", state.StuckOperation, "stuck operation");
                    Check.True(state.StuckSinceUtc.HasValue, "stuck since");

                    // while stuck, other callers fail fast instead of queueing behind it
                    var sw = System.Diagnostics.Stopwatch.StartNew();
                    var fast = Check.Throws<NexusException>(() => AvevaGate.Run(() => 1), "fail fast");
                    if (fast != null)
                    {
                        Check.Equal(503, fast.StatusCode, "fast status");
                        Check.True(fast.Message.Contains("LongWrite"), "names the operation: " + fast.Message);
                    }
                    Check.True(sw.ElapsedMilliseconds < 200, "did not wait");

                    release.Set();
                    for (int i = 0; i < 50 && AvevaGate.State().Stuck; i++) Thread.Sleep(20);
                    Check.True(!AvevaGate.State().Stuck, "cleared once the work ends");
                    Check.Equal(4, AvevaGate.Run(() => 4), "usable again");
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_ui_mode_a_caller_already_waiting_when_the_holder_goes_stuck_does_not_reach_E3D", () =>
            {
                using (var ui = new FakeUiThread())
                {
                    AvevaGate.Reset();
                    ui.RunOnThread(() => AvevaGate.CaptureUiContext(SynchronizationContext.Current));
                    AvevaGate.StuckTimeoutMs = 400;
                    AvevaGate.BusyTimeoutMs = 5000;
                    var release = new ManualResetEventSlim(false);
                    var holder = Task.Run(() =>
                    {
                        try { AvevaGate.Run(() => { release.Wait(5000); return 0; }, "LongWrite"); } catch (NexusException) { }
                    });
                    for (int i = 0; i < 50 && !AvevaGate.State().Busy; i++) Thread.Sleep(10);

                    // B passes the first stuck check (not stuck yet) and waits for the lock
                    bool ran = false;
                    NexusException waiterError = null;
                    var waiter = Task.Run(() =>
                    {
                        try { AvevaGate.Run(() => { ran = true; return 1; }, "Waiter"); }
                        catch (NexusException ex) { waiterError = ex; }
                    });
                    Check.True(waiter.Wait(3000), "waiter returned");
                    Check.True(!ran, "the waiter's work never ran while the stuck work was still running");
                    Check.True(waiterError != null && waiterError.StatusCode == 503, "waiter got 503");

                    release.Set();
                    holder.Wait(3000);
                }
                AvevaGate.Reset();
            });

            Program.Run("Gate_capturing_the_context_does_not_wait_for_a_running_request", () =>
            {
                AvevaGate.Reset();
                var release = new ManualResetEventSlim(false);
                var entered = new ManualResetEventSlim(false);
                var holder = new Thread(() => AvevaGate.Run(() => { entered.Set(); release.Wait(5000); return 0; }));
                holder.Start();
                entered.Wait(5000);
                var capture = new Thread(() => AvevaGate.CaptureUiContext(null));
                capture.Start();
                Check.True(capture.Join(1000), "capture returned while the gate was held");
                release.Set();
                holder.Join();
                AvevaGate.Reset();
            });

            Program.Run("Gate_state_reports_mode_and_waiting_callers", () =>
            {
                AvevaGate.Reset();
                var s = AvevaGate.State();
                Check.Equal("direct", s.Mode, "mode");
                Check.True(!s.Busy && !s.Stuck && s.Waiting == 0, "idle");
                Check.True(s.CurrentOperationSeconds == null, "no age when idle");
            });

            Program.Run("Gate_configure_reads_timeouts_from_the_environment", () =>
            {
                AvevaGate.Reset();
                var env = new System.Collections.Generic.Dictionary<string, string>
                {
                    { "NEXUS_GATE_BUSY_MS", "1234" }, { "NEXUS_GATE_UI_MS", "2345" }, { "NEXUS_GATE_STUCK_MS", "junk" }
                };
                AvevaGate.Configure(n => env.ContainsKey(n) ? env[n] : null);
                Check.Equal(1234, AvevaGate.BusyTimeoutMs, "busy");
                Check.Equal(2345, AvevaGate.UiTimeoutMs, "ui");
                Check.Equal(120000, AvevaGate.StuckTimeoutMs, "invalid keeps the default");
                AvevaGate.Reset();
            });
        }
    }
}
