import { notFound } from "next/navigation";
import { Suspense } from "react";

import { ReviewView } from "@/components/review/review-view";

export default function ReviewPage({ params }: PageProps<"/review/[id]">) {
  return (
    <Suspense fallback={<h1 className="text-2xl font-bold">Review</h1>}>
      {params.then(({ id }) => {
        if (!/^\d+$/.test(id)) notFound();
        return (
          <div className="grid gap-4">
            <h1 className="text-2xl font-bold break-all">Review {id}</h1>
            <ReviewView gameId={Number(id)} />
          </div>
        );
      })}
    </Suspense>
  );
}
