import { SettingsView } from "@/components/settings/settings-view";

export default function SettingsPage() {
  return (
    <div className="grid gap-6">
      <h1 className="text-2xl font-bold">Settings</h1>
      <SettingsView />
    </div>
  );
}
