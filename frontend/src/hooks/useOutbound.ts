import { useState, useEffect, useCallback } from 'react';
import { outboundService, QueueItem } from '../services/outboundService';

export type OutboundTab = 'smart' | 'queue';

export const useOutbound = () => {
  const [activeTab, setActiveTab] = useState<OutboundTab>('smart');
  
  // Smart Dialer State
  const [sheetUrl, setSheetUrl] = useState('');
  const [isSavingUrl, setIsSavingUrl] = useState(false);
  const [isDialing, setIsDialing] = useState(false);
  
  // Queue State
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [isLoadingQueue, setIsLoadingQueue] = useState(false);

  // Initialize
  useEffect(() => {
    const init = async () => {
      const url = await outboundService.getSheetUrl();
      setSheetUrl(url);
    };
    init();
  }, []);

  // Refresh Queue
  const refreshQueue = useCallback(async () => {
    setIsLoadingQueue(true);
    try {
      const data = await outboundService.getQueueStatus();
      setQueue(data);
    } catch (error) {
      console.error('Failed to fetch queue:', error);
    } finally {
      setIsLoadingQueue(false);
    }
  }, []);

  // Poll queue
  useEffect(() => {
    refreshQueue();
    const interval = setInterval(refreshQueue, 10000);
    return () => clearInterval(interval);
  }, [refreshQueue]);

  // Handle Save Sheet URL
  const handleSaveSheetUrl = async () => {
    setIsSavingUrl(true);
    try {
      const success = await outboundService.saveSheetUrl(sheetUrl);
      if (success) {
        alert('Google Sheet URL saved successfully!');
      } else {
        alert('Failed to save URL.');
      }
    } catch (error: any) {
      alert(`Error: ${error.message}`);
    } finally {
      setIsSavingUrl(false);
    }
  };

  // Handle Start Campaign
  const handleStartBulkCampaign = async () => {
    if (!sheetUrl) {
      alert('Please enter a Google Sheet URL first.');
      return;
    }
    
    setIsDialing(true);
    try {
      await outboundService.triggerBulkCampaign();
      alert('Outbound campaign started! Neha will now call everyone row-by-row.');
      setActiveTab('queue');
      refreshQueue();
    } catch (error: any) {
      alert(`Error starting campaign: ${error.message}`);
    } finally {
      setIsDialing(false);
    }
  };

  return {
    activeTab,
    setActiveTab,
    sheetUrl,
    setSheetUrl,
    isSavingUrl,
    handleSaveSheetUrl,
    isDialing,
    handleStartBulkCampaign,
    queue,
    isLoadingQueue,
    refreshQueue
  };
};
