import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";

describe("web app manifest", () => {
  const appManifest = manifest();

  it("is installable as a standalone app", () => {
    expect(appManifest.name).toBe("Chess Tutor");
    expect(appManifest.short_name).toBeTruthy();
    expect(appManifest.start_url).toBe("/");
    expect(appManifest.display).toBe("standalone");
  });

  it("ships 192px and 512px icons that exist in public/", () => {
    const sizes = appManifest.icons?.map((icon) => icon.sizes);
    expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    for (const icon of appManifest.icons ?? []) {
      expect(existsSync(join(process.cwd(), "public", icon.src))).toBe(true);
    }
  });
});
