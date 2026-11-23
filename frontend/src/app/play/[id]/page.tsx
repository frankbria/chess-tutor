import { Suspense } from "react";

export default function PlayPage({ params }: PageProps<"/play/[id]">) {
  return (
    <Suspense fallback={<h1 className="text-2xl font-bold">Game</h1>}>
      {params.then(({ id }) => (
        <h1 className="text-2xl font-bold break-all">
          Game {id}
        </h1>
      ))}
    </Suspense>
  );
}
