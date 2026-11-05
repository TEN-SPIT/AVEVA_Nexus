using System;
using System.Collections.Generic;

namespace Nexus.Tests
{
    public static class Check
    {
        public static int Failures;

        public static void Equal<T>(T expected, T actual, string name)
        {
            if (!EqualityComparer<T>.Default.Equals(expected, actual))
                Fail(name, "expected <" + expected + "> but was <" + actual + ">");
        }

        public static void True(bool condition, string name)
        {
            if (!condition) Fail(name, "expected true");
        }

        // Returns the exception if one of type TEx was thrown, otherwise records a failure and returns null.
        public static TEx Throws<TEx>(Action act, string name) where TEx : Exception
        {
            try { act(); }
            catch (TEx ex) { return ex; }
            catch (Exception ex) { Fail(name, "threw " + ex.GetType().Name + " instead of " + typeof(TEx).Name); return null; }
            Fail(name, "expected " + typeof(TEx).Name + " but nothing was thrown");
            return null;
        }

        // Every failure message, in order (Program.Run attaches the new ones to the current test for the JUnit report).
        public static readonly List<string> Messages = new List<string>();

        public static void Fail(string name, string detail)
        {
            Failures++;
            Messages.Add(name + ": " + detail);
            Console.WriteLine("  FAIL  " + name + ": " + detail);
        }
    }
}
