using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using System.Xml.Linq;

namespace Nexus.Tests
{
    // Test classes are static classes named "*Tests" with a public static RunAll() that calls Program.Run(name, body).
    // Usage: Nexus.Tests.exe [--junit <path>]   (exit code 0 only if every check passed)
    public static class Program
    {
        static int _passed;

        sealed class Outcome
        {
            public string Suite, Name;
            public double Seconds;
            public List<string> Failures;
        }

        static readonly List<Outcome> Outcomes = new List<Outcome>();
        static string _suite = "";

        public static void Run(string name, Action test)
        {
            int before = Check.Failures;
            int messagesBefore = Check.Messages.Count;
            var watch = Stopwatch.StartNew();
            try { test(); }
            catch (Exception ex) { Check.Fail(name, "unexpected " + ex.GetType().Name + ": " + ex.Message); }
            watch.Stop();
            if (Check.Failures == before) { _passed++; Console.WriteLine("  ok    " + name); }
            Outcomes.Add(new Outcome
            {
                Suite = _suite,
                Name = name,
                Seconds = watch.Elapsed.TotalSeconds,
                Failures = Check.Messages.Skip(messagesBefore).ToList()
            });
        }

        public static int Main(string[] args)
        {
            var suites = Assembly.GetExecutingAssembly().GetTypes()
                .Where(t => t.IsClass && t.IsAbstract && t.IsSealed && t.Name.EndsWith("Tests"))
                .OrderBy(t => t.Name, StringComparer.Ordinal);
            foreach (var t in suites)
            {
                _suite = t.Name;
                t.GetMethod("RunAll", BindingFlags.Public | BindingFlags.Static)?.Invoke(null, null);
            }

            int junit = Array.IndexOf(args, "--junit");
            if (junit >= 0 && junit + 1 < args.Length)
            {
                WriteJUnit(args[junit + 1]);
            }

            Console.WriteLine(_passed + " passed, " + Check.Failures + " failed");
            return Check.Failures == 0 ? 0 : 1;
        }

        static void WriteJUnit(string path)
        {
            var inv = System.Globalization.CultureInfo.InvariantCulture;
            var root = new XElement("testsuites",
                new XAttribute("tests", Outcomes.Count),
                new XAttribute("failures", Outcomes.Count(o => o.Failures.Count > 0)));
            foreach (var group in Outcomes.GroupBy(o => o.Suite))
            {
                var suite = new XElement("testsuite",
                    new XAttribute("name", "Nexus.Tests." + group.Key),
                    new XAttribute("tests", group.Count()),
                    new XAttribute("failures", group.Count(o => o.Failures.Count > 0)),
                    new XAttribute("time", group.Sum(o => o.Seconds).ToString("0.000", inv)));
                foreach (Outcome o in group)
                {
                    var testcase = new XElement("testcase",
                        new XAttribute("classname", "Nexus.Tests." + o.Suite),
                        new XAttribute("name", o.Name),
                        new XAttribute("time", o.Seconds.ToString("0.000", inv)));
                    if (o.Failures.Count > 0)
                    {
                        testcase.Add(new XElement("failure",
                            new XAttribute("message", o.Failures[0]),
                            string.Join("\n", o.Failures)));
                    }
                    suite.Add(testcase);
                }
                root.Add(suite);
            }
            new XDocument(new XDeclaration("1.0", "utf-8", null), root).Save(path);
        }
    }

    public static class RunnerSelfTests
    {
        public static void RunAll()
        {
            Program.Run("runner works", () => Check.Equal(1, 1, "one"));
        }
    }
}
