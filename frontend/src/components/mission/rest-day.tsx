import Link from "next/link";
import { Coffee } from "lucide-react";
import { withTrack, type Track } from "@/lib/tracks";

/** Saturday and Sunday: no lesson is generated. The streak is safe, and open lessons can still be finished. */
export function RestDay({ track = "ai" }: { track?: Track }) {
  return (
    <div className="rounded-lg border border-dashed p-8 text-center">
      <Coffee className="mx-auto mb-3 size-6 text-primary" />
      <h2 className="text-lg font-semibold">Weekend: no lesson today</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
        Lessons run Monday to Friday. Your streak does not break over the weekend. If a lesson from the week is still
        open (yellow), you can finish it from the{" "}
        <Link href={withTrack("/calendar", track)} className="text-primary underline-offset-4 hover:underline">calendar</Link>.
      </p>
    </div>
  );
}
