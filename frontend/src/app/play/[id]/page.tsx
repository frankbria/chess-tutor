import { Suspense } from "react";

import { PlayBoard } from "@/components/play/play-board";

export default function PlayPage({ params }: PageProps<"/play/[id]">) {
  return (
    <Suspense fallback={<h1 className="text-2xl font-bold">Game</h1>}>
      {params.then(({ id }) => (
        <>
          <h1 className="mb-4 text-2xl font-bold break-all">Game {id}</h1>
          <PlayBoard gameId={Number(id)} />
        </>
      ))}
    </Suspense>
  );
}
