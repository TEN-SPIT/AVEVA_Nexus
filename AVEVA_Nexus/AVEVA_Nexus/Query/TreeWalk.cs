using System;
using System.Collections.Generic;

namespace AVEVA_Nexus.Query
{
    // Depth-first (pre-order) walk below a root, used by GET /api/Element/Tree. The root itself is not listed;
    // its members have depth 1. "include" only decides which nodes are listed: the walk goes on through the
    // others. Stops after "limit" listed nodes; returns true (truncated) if at least one more would have been
    // listed. Pure logic: no Aveva.* references (the provider runs it inside AvevaGate with DbElements).
    public static class TreeWalk
    {
        public static bool Walk<T>(
            T root,
            Func<T, IEnumerable<T>> members,
            Func<T, bool> include,
            int maxDepth,
            int limit,
            Action<T, T, int> emit)   // node, owner, depth
        {
            int listed = 0;
            bool truncated = false;
            Visit(root, 1);
            return truncated;

            void Visit(T owner, int depth)
            {
                if (depth > maxDepth || truncated) return;
                foreach (T node in members(owner))
                {
                    if (include(node))
                    {
                        if (listed >= limit) { truncated = true; return; }
                        listed++;
                        emit(node, owner, depth);
                    }
                    Visit(node, depth + 1);
                    if (truncated) return;
                }
            }
        }
    }
}
