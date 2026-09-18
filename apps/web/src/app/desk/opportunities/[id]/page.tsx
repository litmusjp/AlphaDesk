import { AIResearchPanel } from "@/components/ai-research-panel";
import { OrderReview } from "@/components/order-review";
import { WorkspaceShell } from "@/components/workspace-shell";
export default async function Opportunity({params}:{params:Promise<{id:string}>}){const {id}=await params;return <WorkspaceShell title="Paper Order Review" description="Review real evidence and explicitly approve a bounded next-session paper order."><OrderReview id={id}/><AIResearchPanel opportunityId={id}/></WorkspaceShell>}
