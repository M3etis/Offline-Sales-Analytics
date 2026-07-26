import React, { createContext, useContext, useState, useMemo, useCallback } from 'react';
import type { ReactNode } from 'react';

interface DatasetContextType {
  selectedDatasetId: string | null;
  setSelectedDatasetId: (id: string | null) => void;
  selectedGroupId: string | null;
  setSelectedGroupId: (id: string | null) => void;
}

const DatasetContext = createContext<DatasetContextType | undefined>(undefined);

export const DatasetProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [selectedDatasetId, setSelectedDatasetIdState] = useState<string | null>(() => {
    return localStorage.getItem('selectedDatasetId');
  });
  const [selectedGroupId, setSelectedGroupIdState] = useState<string | null>(() => {
    return localStorage.getItem('selectedGroupId');
  });

  const setSelectedDatasetId = useCallback((id: string | null) => {
    setSelectedDatasetIdState(id);
    if (id) {
      localStorage.setItem('selectedDatasetId', id);
    } else {
      localStorage.removeItem('selectedDatasetId');
    }
  }, []);

  const setSelectedGroupId = useCallback((id: string | null) => {
    setSelectedGroupIdState(id);
    if (id) {
      localStorage.setItem('selectedGroupId', id);
    } else {
      localStorage.removeItem('selectedGroupId');
    }
  }, []);

  const value = useMemo(() => ({
    selectedDatasetId, setSelectedDatasetId, selectedGroupId, setSelectedGroupId
  }), [selectedDatasetId, setSelectedDatasetId, selectedGroupId, setSelectedGroupId]);

  return (
    <DatasetContext.Provider value={value}>
      {children}
    </DatasetContext.Provider>
  );
};

export const useDataset = () => {
  const context = useContext(DatasetContext);
  if (context === undefined) {
    throw new Error('useDataset must be used within a DatasetProvider');
  }
  return context;
};
