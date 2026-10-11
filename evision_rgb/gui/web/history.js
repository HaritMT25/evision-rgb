"use strict";
/* Undo/redo history of editor snapshots (custom-key colours, layer stacks).
   Snapshots are deep copies, both when stored and when handed back, so later edits can't change history.
   Works in the browser (window.EVHistory) and in node (require) for the tests. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.EVHistory = factory();
}(typeof self !== "undefined" ? self : this, function () {
  const copy = s => JSON.parse(JSON.stringify(s));

  function create(initial, limit = 100) {
    let stack = [copy(initial)], i = 0;
    return {
      /** record the state after an edit (identical consecutive states are recorded once) */
      push(s) {
        const snap = copy(s);
        if (JSON.stringify(snap) === JSON.stringify(stack[i])) return false;
        stack = stack.slice(0, i + 1);  // a new edit discards what could have been redone
        stack.push(snap);
        if (stack.length > limit + 1) stack.shift();
        i = stack.length - 1;
        return true;
      },
      undo() { if (i === 0) return null; i -= 1; return copy(stack[i]); },
      redo() { if (i >= stack.length - 1) return null; i += 1; return copy(stack[i]); },
      canUndo() { return i > 0; },
      canRedo() { return i < stack.length - 1; },
      current() { return copy(stack[i]); },
    };
  }

  return { create };
}));
