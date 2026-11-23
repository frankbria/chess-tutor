import { Suspense } from "react";

export default function ReviewPage({ params }: PageProps<"/review/[id]">) {
  return (
    <Suspense fallback={<h1 className="text-2xl font-bold">Review</h1>}>
      {params.then(({ id }) => (
        <h1 className="text-2xl font-bold break-all">
          Review {id}
        </h1>
      ))}
    </Suspense>
  );
}
