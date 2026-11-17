"use client";

import { AnimatePresence, motion } from "framer-motion";
import { NotebookPen } from "lucide-react";
import { NotebookDrawer } from "./notebook-drawer";
import { useNotebook } from "./notebook-context";

/** Global floating notebook button. Rendered once in the root layout, so it exists on every screen. */
export function NotebookFab() {
  const { open, openNotebook, activeTaskId } = useNotebook();
  return (
    <AnimatePresence>
      {!open && (
        <motion.button
          key="notebook-fab" type="button" onClick={openNotebook} aria-label="Open notebook"
          initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }}
          whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.94 }}
          className="fixed bottom-[calc(4.75rem+env(safe-area-inset-bottom))] right-4 z-[9999] grid size-12 md:bottom-8 md:right-8 md:size-14 place-items-center rounded-full border border-emerald-300/40 bg-[#0b0d14]/80 text-white shadow-[0_0_24px_rgba(52,211,153,0.35)] backdrop-blur-xl"
        >
          {/* pulsing ring, drawn by framer-motion so it respects reduced-motion via MotionConfig */}
          <motion.span aria-hidden className="pointer-events-none absolute inset-0 rounded-full border border-emerald-300/60"
            animate={{ scale: [1, 1.5], opacity: [0.7, 0] }} transition={{ duration: 2, repeat: Infinity, ease: "easeOut" }} />
          <NotebookPen className="size-6" />
          {activeTaskId && <span aria-hidden className="absolute right-1 top-1 size-2.5 rounded-full bg-emerald-400 ring-2 ring-[#0b0d14]" />}
        </motion.button>
      )}
    </AnimatePresence>
  );
}

/** Global drawer. Keyed by task so switching chapters starts a fresh load and never shows another task's notes. */
export function NotebookDrawerHost() {
  const { open, closeNotebook, activeTaskId, setActiveChapterTitle } = useNotebook();
  return <NotebookDrawer key={activeTaskId ?? "none"} taskId={activeTaskId} open={open} onClose={closeNotebook} onTitle={setActiveChapterTitle} />;
}
