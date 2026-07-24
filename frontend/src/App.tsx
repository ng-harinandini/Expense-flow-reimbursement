import React, { useState, useEffect } from 'react';
import { ExpenseClaim, UserRole, Employee, PolicyRuleDefinition, AuditLogEntry } from './types';
import { INITIAL_EMPLOYEES } from './data/initialClaims';
import { Header } from './components/Header';
import { ClaimsList } from './components/ClaimsList';
import { ExpenseSubmitModal } from './components/ExpenseSubmitModal';
import { ClaimDetailModal } from './components/ClaimDetailModal';
import { PolicyEngineView } from './components/PolicyEngineView';
import { PolicyGuidelinesReadonly } from './components/PolicyGuidelinesReadonly';
import { ArchitectureOverview } from './components/ArchitectureOverview';
import { WorkflowVisualizer } from './components/WorkflowVisualizer';
import { AwsSandboxRefiner } from './components/AwsSandboxRefiner';
import { AuditLogsView } from './components/AuditLogsView';

export default function App() {
  const [employees] = useState<Employee[]>(INITIAL_EMPLOYEES);
  const [currentEmployee, setCurrentEmployee] = useState<Employee>(INITIAL_EMPLOYEES[0]);
  const [currentRole, setCurrentRole] = useState<UserRole>('employee');

  const [activeTab, setActiveTab] = useState<string>('workspace');
  const [isArchInspectorOpen, setIsArchInspectorOpen] = useState<boolean>(false);

  const [claims, setClaims] = useState<ExpenseClaim[]>([]);
  const [policyRules, setPolicyRules] = useState<PolicyRuleDefinition[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);

  const [selectedClaim, setSelectedClaim] = useState<ExpenseClaim | null>(null);

  // Fetch initial data from Express backend
  const fetchData = async () => {
    try {
      const [claimsRes, rulesRes, auditRes] = await Promise.all([
        fetch('/api/claims'),
        fetch('/api/policy-rules'),
        fetch('/api/audit-logs')
      ]);

      if (claimsRes.ok) setClaims(await claimsRes.json());
      if (rulesRes.ok) setPolicyRules(await rulesRes.json());
      if (auditRes.ok) setAuditLogs(await auditRes.json());
    } catch (err) {
      console.error('Error fetching initial backend data:', err);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleRoleChange = (role: UserRole, emp: Employee) => {
    setCurrentRole(role);
    setCurrentEmployee(emp);
  };

  // Submit Claim handler
  const handleSubmitClaim = async (claimData: Partial<ExpenseClaim>) => {
    try {
      const res = await fetch('/api/claims', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(claimData)
      });
      if (res.ok) {
        await fetchData();
        if (currentRole === 'employee') {
          setActiveTab('my-claims');
        } else {
          setActiveTab('claims');
        }
      }
    } catch (err) {
      console.error('Error submitting claim:', err);
    }
  };

  // Execute Action (Approve / Reject / Disburse / Flag) handler
  const handleExecuteAction = async (action: 'APPROVE' | 'REJECT' | 'DISBURSE' | 'FLAG_FRAUD', notes: string) => {
    if (!selectedClaim) return;

    try {
      const res = await fetch(`/api/claims/${selectedClaim.id}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          actorName: currentEmployee.name,
          actorRole: currentRole,
          notes
        })
      });

      if (res.ok) {
        const updatedClaim = await res.json();
        setSelectedClaim(updatedClaim);
        await fetchData();
      }
    } catch (err) {
      console.error('Error executing claim action:', err);
    }
  };

  // Update Policy Rules
  const handleUpdateRules = async (newRules: PolicyRuleDefinition[]) => {
    try {
      const res = await fetch('/api/policy-rules', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newRules)
      });
      if (res.ok) {
        await fetchData();
      }
    } catch (err) {
      console.error('Error updating policy rules:', err);
    }
  };

  // Calculate live stats
  const pendingCount = claims.filter(c => ['Manager_Review', 'Finance_Review', 'Submitted'].includes(c.status)).length;
  const flaggedCount = claims.filter(c => c.status === 'Flagged_Fraud' || c.fraudScreening?.isFlagged).length;
  const autoApprovedCount = claims.filter(c => c.status === 'Auto_Approved').length;
  const autoApproveRate = claims.length > 0 ? Math.round((autoApprovedCount / claims.length) * 100) : 0;
  const totalDisbursedUSD = claims
    .filter(c => c.status === 'Disbursed' || c.status === 'Auto_Approved' || c.status === 'Approved')
    .reduce((acc, c) => acc + c.amountUSD, 0);

  // Compute filtered claims based on role active tab
  const getRoleFilteredClaims = () => {
    if (activeTab === 'my-claims') {
      return claims.filter(c => c.employeeId === currentEmployee.id);
    }
    if (activeTab === 'approvals') {
      return claims.filter(c => ['Manager_Review', 'Submitted', 'Flagged_Fraud'].includes(c.status));
    }
    if (activeTab === 'disbursements') {
      return claims.filter(c => ['Approved', 'Finance_Review', 'Disbursed', 'Auto_Approved'].includes(c.status));
    }
    return claims;
  };

  return (
    <div className="min-h-screen bg-[#020617] text-slate-100 flex flex-col font-sans antialiased selection:bg-sky-500 selection:text-slate-950">
      {/* Top Header */}
      <Header
        currentRole={currentRole}
        currentEmployee={currentEmployee}
        employees={employees}
        onRoleChange={handleRoleChange}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        isArchInspectorOpen={isArchInspectorOpen}
        setIsArchInspectorOpen={setIsArchInspectorOpen}
        stats={{
          pendingCount,
          flaggedCount,
          autoApproveRate,
          totalDisbursedUSD
        }}
      />

      {/* Main Content Body */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Role Screens */}
        {activeTab === 'workspace' && (
          <ExpenseSubmitModal
            currentEmployee={currentEmployee}
            onSubmitClaim={handleSubmitClaim}
          />
        )}

        {(activeTab === 'my-claims' || activeTab === 'team-claims' || activeTab === 'approvals' || activeTab === 'disbursements' || activeTab === 'claims') && (
          <ClaimsList
            claims={getRoleFilteredClaims()}
            onSelectClaim={(claim) => setSelectedClaim(claim)}
            onOpenSubmitModal={currentRole === 'employee' ? () => setActiveTab('workspace') : undefined}
          />
        )}

        {activeTab === 'policy-info' && (
          <PolicyGuidelinesReadonly rules={policyRules} />
        )}

        {activeTab === 'policy' && (
          <PolicyEngineView
            policyRules={policyRules}
            onUpdateRules={handleUpdateRules}
          />
        )}

        {/* Architecture & Inspector Views */}
        {activeTab === 'architecture' && (
          <ArchitectureOverview />
        )}

        {activeTab === 'workflow' && (
          <WorkflowVisualizer />
        )}

        {activeTab === 'iam' && (
          <AwsSandboxRefiner />
        )}

        {activeTab === 'audit' && (
          <AuditLogsView logs={auditLogs} />
        )}
      </main>

      {/* Modal for detailed claim inspection */}
      {selectedClaim && (
        <ClaimDetailModal
          claim={selectedClaim}
          currentRole={currentRole}
          currentActorName={currentEmployee.name}
          onClose={() => setSelectedClaim(null)}
          onExecuteAction={handleExecuteAction}
        />
      )}

      {/* Footer */}
      <footer className="bg-[#020617] border-t border-slate-800 text-slate-500 text-[11px] font-mono py-4">
        <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row justify-between items-center gap-2">
          <div>ExpenseFlow Orchestrator • Enterprise Serverless Architecture</div>
          <div className="flex items-center space-x-4">
            <span>Gemini 2.5 Server-side</span>
            <span>•</span>
            <span>AWS Step Functions Simulator</span>
            <span>•</span>
            <span>RBAC & Audit Active</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
