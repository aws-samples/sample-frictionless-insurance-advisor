import { createContext, useContext, type ReactNode } from 'react';

const AdvisorContext = createContext<string | null>(null);

/** Makes the signed-in advisor's email available to the pages (for PDFs). */
export function AdvisorProvider({
  advisorEmail,
  children,
}: {
  advisorEmail: string | null;
  children: ReactNode;
}) {
  return <AdvisorContext.Provider value={advisorEmail}>{children}</AdvisorContext.Provider>;
}

/** The signed-in advisor's email, or null if unknown. */
export function useAdvisorEmail(): string | null {
  return useContext(AdvisorContext);
}
