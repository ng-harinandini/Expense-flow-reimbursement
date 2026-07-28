import { Employee } from '../types';

/**
 * Seed data for the Organisation employee directory.
 * TODO: replace with GET /api/employees once the endpoint exists.
 */
export const INITIAL_EMPLOYEES_DIRECTORY: Employee[] = [
  {
    id: 'emp-101',
    name: 'Sarah Chen',
    email: 'sarah.chen@acme.com',
    grade: 'L2',
    role: 'employee',
    status: 'active',
    managerId: 'emp-201',
    managerName: 'David Miller',
    monthlySpendUSD: 240
  },
  {
    id: 'emp-102',
    name: 'Marcus Brody',
    email: 'marcus.brody@acme.com',
    grade: 'L4',
    role: 'employee',
    status: 'active',
    managerId: 'emp-201',
    managerName: 'David Miller',
    monthlySpendUSD: 890
  },
  {
    id: 'emp-103',
    name: 'Priya Raman',
    email: 'priya.raman@acme.com',
    grade: 'L3',
    role: 'employee',
    status: 'active',
    managerId: 'emp-201',
    managerName: 'David Miller',
    monthlySpendUSD: 615
  },
  {
    id: 'emp-104',
    name: 'Tomas Novak',
    email: 'tomas.novak@acme.com',
    grade: 'L1',
    role: 'employee',
    status: 'inactive',
    managerId: 'emp-203',
    managerName: 'Elena Rossi',
    monthlySpendUSD: 95
  },
  {
    id: 'emp-105',
    name: 'Aisha Bello',
    email: 'aisha.bello@acme.com',
    grade: 'L3',
    role: 'employee',
    status: 'active',
    managerId: 'emp-202',
    managerName: 'Grace Kim',
    monthlySpendUSD: 1320
  },
  {
    id: 'emp-106',
    name: 'Liam Fitzgerald',
    email: 'liam.fitzgerald@acme.com',
    grade: 'L2',
    role: 'employee',
    status: 'active',
    managerId: 'emp-202',
    managerName: 'Grace Kim',
    monthlySpendUSD: 430
  },
  {
    id: 'emp-201',
    name: 'David Miller',
    email: 'david.miller@acme.com',
    grade: 'L5',
    role: 'manager',
    status: 'active',
    managerId: 'emp-301',
    managerName: 'Alex Vance',
    monthlySpendUSD: 1450
  },
  {
    id: 'emp-202',
    name: 'Grace Kim',
    email: 'grace.kim@acme.com',
    grade: 'L5',
    role: 'manager',
    status: 'active',
    managerId: 'emp-302',
    managerName: 'Nadia Haddad',
    monthlySpendUSD: 2180
  },
  {
    id: 'emp-203',
    name: 'Elena Rossi',
    email: 'elena.rossi@acme.com',
    grade: 'L4',
    role: 'manager',
    status: 'active',
    managerId: 'emp-302',
    managerName: 'Nadia Haddad',
    monthlySpendUSD: 760
  },
  {
    id: 'emp-301',
    name: 'Alex Vance',
    email: 'alex.vance@acme.com',
    grade: 'Director',
    role: 'finance',
    status: 'active',
    monthlySpendUSD: 3100
  },
  {
    id: 'emp-302',
    name: 'Nadia Haddad',
    email: 'nadia.haddad@acme.com',
    grade: 'VP',
    role: 'admin',
    status: 'active',
    monthlySpendUSD: 4260
  },
  {
    id: 'emp-303',
    name: 'Ken Watanabe',
    email: 'ken.watanabe@acme.com',
    grade: 'Director',
    role: 'auditor',
    status: 'active',
    managerId: 'emp-302',
    managerName: 'Nadia Haddad',
    monthlySpendUSD: 540
  }
];
