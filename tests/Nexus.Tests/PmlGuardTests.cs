using System.Collections.Generic;
using System.Linq;
using AVEVA_Nexus.Scripts;

namespace Nexus.Tests
{
    // Verified on E3D 3.1: a "handle any" must come IMMEDIATELY after the statement it protects (a handle after
    // a whole block, or at the end of a macro, catches nothing), and "return" inside it ends the macro.
    public static class PmlGuardTests
    {
        static readonly string[] Handler =
        {
            "handle any",
            "!!nexusfailed = TRUE",
            "!!nexusmsg = !!error.text",
            "return",
            "endhandle",
        };

        static List<string> Wrap(params string[] lines) { return PmlGuard.Wrap(lines); }

        static string Joined(IEnumerable<string> lines) { return string.Join("|", lines); }

        public static void RunAll()
        {
            Program.Run("Guard_starts_by_resetting_the_failure_flag_and_message", () =>
            {
                var w = Wrap("!!x = 1");
                Check.Equal("!!nexusfailed = FALSE", w[0], "flag");
                Check.Equal("!!nexusmsg = ''", w[1], "message");
            });

            Program.Run("Guard_puts_the_handler_right_after_a_plain_statement", () =>
            {
                var w = Wrap("NEW ZONE /X");
                Check.Equal("NEW ZONE /X|" + Joined(Handler), Joined(w.Skip(2)), "statement then handler");
            });

            Program.Run("Guard_protects_every_statement_inside_a_block_but_not_the_structure_lines", () =>
            {
                var w = Wrap("do !i from 1 to 2", "NEW EQUI /A", "enddo");
                Check.Equal(
                    "do !i from 1 to 2|NEW EQUI /A|" + Joined(Handler) + "|enddo",
                    Joined(w.Skip(2)), "block");
            });

            Program.Run("Guard_does_not_add_a_handler_after_if_else_endif", () =>
            {
                var w = Wrap("if 1 eq 1 then", "!a = 1", "elseif 2 eq 2 then", "!a = 2", "else", "!a = 3", "endif");
                int handlers = w.Count(l => l == "handle any");
                Check.Equal(3, handlers, "one per statement");
                Check.Equal("if 1 eq 1 then", w[2], "if untouched");
                Check.True(w.Contains("elseif 2 eq 2 then") && w.Contains("else") && w.Contains("endif"), "structure kept");
            });

            Program.Run("Guard_respects_a_handler_the_user_wrote_right_after_the_statement", () =>
            {
                var w = Wrap("!a = !!nofunc()", "handle any", "$P mine", "endhandle");
                Check.Equal(1, w.Count(l => l == "handle any"), "only the user's handler");
                Check.True(!w.Contains("!!nexusfailed = TRUE"), "ours not added");
            });

            Program.Run("Guard_does_not_wrap_the_statements_inside_a_user_handler", () =>
            {
                var w = Wrap("handle any", "$P one", "$P two", "endhandle", "!!y = 2");
                Check.Equal(2, w.Count(l => l == "handle any"), "user's handler + one for !!y");
                Check.Equal("handle any|$P one|$P two|endhandle|!!y = 2|" + Joined(Handler), Joined(w.Skip(2)), "shape");
            });

            Program.Run("Guard_handles_case_and_indentation_of_keywords", () =>
            {
                var w = Wrap("  DO !i FROM 1 TO 2", "  !a = 1", "  ENDDO");
                Check.Equal(1, w.Count(l => l == "handle any"), "one statement");
            });

            Program.Run("Guard_does_not_mistake_names_for_keywords", () =>
            {
                // 'done', 'doubled' and 'iffy' are statements, not block structure
                var w = Wrap("done = 1", "!doubled = 2", "iffy");
                Check.Equal(3, w.Count(l => l == "handle any"), "three statements");
            });

            Program.Run("Guard_leaves_break_skip_and_return_alone", () =>
            {
                var w = Wrap("do !i from 1 to 3", "break", "skip", "enddo", "return");
                Check.Equal(0, w.Count(l => l == "handle any"), "no handlers");
            });

            Program.Run("Guard_removes_nothing_the_user_wrote", () =>
            {
                var input = new[] { "!a = 1", "if !a eq 1 then", "$P hi", "endif" };
                var w = PmlGuard.Wrap(input);
                foreach (var l in input) Check.True(w.Contains(l), "kept: " + l);
            });
        }
    }
}
