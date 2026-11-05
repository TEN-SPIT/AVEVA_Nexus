using System;
using System.Collections.Concurrent;
using System.Threading;
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
        }
    }
}
