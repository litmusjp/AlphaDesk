import { ConditionalApprovals } from "@/components/conditional-approvals";
import { WorkspaceShell } from "@/components/workspace-shell";

export default function ApprovalsPage() {
  return (
    <WorkspaceShell
      mode="desk"
      title="Pre-approved Orders"
      description="Review bounded paper approvals before the next U.S. regular session."
    >
      <ConditionalApprovals />
    </WorkspaceShell>
  );
}
