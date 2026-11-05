using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Scripts;

namespace Nexus.Tests
{
    public static class ScriptSplitterTests
    {
        public static void RunAll()
        {
            Program.Run("Split_plain_lines_become_single_units_with_line_numbers", () =>
            {
                var units = ScriptSplitter.Split("!!a = 1\n\n!!b = 2");
                Check.Equal(2, units.Count, "count");
                Check.True(!units[0].IsBlock && !units[1].IsBlock, "not blocks");
                Check.Equal(1, units[0].FirstLine, "first line of unit 1");
                Check.Equal(3, units[1].FirstLine, "blank lines keep the original numbering");
                Check.Equal("!!b = 2", units[1].Lines[0], "text");
            });

            Program.Run("Split_if_else_endif_is_one_block", () =>
            {
                var units = ScriptSplitter.Split("!!a = 5\nif (!!a gt 3) then\n  !!b = |big|\nelse\n  !!b = |small|\nendif\n!!z = 1");
                Check.Equal(3, units.Count, "count");
                Check.True(units[1].IsBlock, "middle unit is a block");
                Check.Equal(5, units[1].Lines.Count, "block lines");
                Check.Equal(2, units[1].FirstLine, "block first line");
                Check.Equal(6, units[1].LastLine, "block last line");
                Check.True(!units[2].IsBlock && units[2].Lines[0] == "!!z = 1", "line after the block");
            });

            Program.Run("Split_nested_blocks_stay_in_one_unit", () =>
            {
                var units = ScriptSplitter.Split("do !i from 1 to 3\n  if (!i eq 2) then\n    !!x = !i\n  endif\nenddo");
                Check.Equal(1, units.Count, "one unit");
                Check.Equal(5, units[0].Lines.Count, "all lines");
            });

            Program.Run("Split_handle_block_and_case_insensitive_keywords", () =>
            {
                var units = ScriptSplitter.Split("HANDLE ANY\n  !!a = 1\nENDHANDLE\nIf (1 eq 1) Then\n!!b = 2\nEndIf\nDo !i From 1 To 2\nEndDo");
                Check.Equal(3, units.Count, "three blocks");
                Check.True(units[0].IsBlock && units[1].IsBlock && units[2].IsBlock, "all blocks");
            });

            Program.Run("Split_drops_blank_and_comment_only_lines", () =>
            {
                var units = ScriptSplitter.Split("-- a comment\n!!a = 1\n   \n  -- another\n");
                Check.Equal(1, units.Count, "count");
                Check.Equal(2, units[0].FirstLine, "numbering counts the dropped lines");
            });

            Program.Run("Split_does_not_mistake_names_for_block_keywords", () =>
            {
                var units = ScriptSplitter.Split("!!doit = 1\nifx = 2\ndone = 3\nhandler = 4\nendifx = 5");
                Check.Equal(5, units.Count, "five plain lines");
                Check.True(!units[0].IsBlock && !units[1].IsBlock && !units[2].IsBlock && !units[3].IsBlock && !units[4].IsBlock, "no blocks");
            });

            Program.Run("Split_rejects_an_unterminated_block", () =>
            {
                var ex = Check.Throws<NexusException>(() => ScriptSplitter.Split("!!a = 1\nif (!!a eq 1) then\n!!b = 2"), "unterminated");
                if (ex != null)
                {
                    Check.Equal(400, ex.StatusCode, "status");
                    Check.True(ex.Message.Contains("line 2"), "message names the line: " + ex.Message);
                }
            });

            Program.Run("Split_rejects_a_stray_block_end", () =>
            {
                var ex = Check.Throws<NexusException>(() => ScriptSplitter.Split("!!a = 1\nendif"), "stray endif");
                if (ex != null)
                {
                    Check.Equal(400, ex.StatusCode, "status");
                    Check.True(ex.Message.Contains("line 2"), "message names the line: " + ex.Message);
                }
            });

            Program.Run("Split_empty_script_gives_no_units", () =>
                Check.Equal(0, ScriptSplitter.Split("  \n\n").Count, "count"));
        }
    }
}
