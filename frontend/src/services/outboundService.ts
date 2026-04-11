const API_BASE = import.meta.env.VITE_API_URL || '';

export interface QueueItem {
  id: string;
  phone: string;
  status: 'pending' | 'calling' | 'success' | 'failed' | 'cancelled';
  timestamp: string;
  error?: string;
}

/**
 * Service to handle outbound calling API interactions.
 */
export const outboundService = {
  /**
   * Triggers a single manual outbound call.
   */
  async triggerManualCall(phone: string): Promise<{ success: boolean; message: string }> {
    try {
      const response = await fetch(`${API_BASE}/api/outbound/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number: phone }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to trigger call');
      }

      return await response.json();
    } catch (error) {
      console.error('Error in triggerManualCall:', error);
      throw error;
    }
  },

  /**
   * Triggers the specialized bulk_dialer.py logic via API.
   */
  async triggerBulkCampaign(): Promise<{ success: boolean; message: string }> {
    try {
      const response = await fetch(`${API_BASE}/api/outbound/bulk-dial`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error('Failed to start campaign');
      return await response.json();
    } catch (error) {
      console.error('Error in triggerBulkCampaign:', error);
      throw error;
    }
  },

  /**
   * Fetches the current Google Sheet URL from the backend.
   */
  async getSheetUrl(): Promise<string> {
    try {
      const response = await fetch(`${API_BASE}/api/knowledge/status`);
      if (!response.ok) return '';
      const data = await response.json();
      return data.sheet_url || '';
    } catch (error) {
      console.error('Error fetching sheet URL:', error);
      return '';
    }
  },

  /**
   * Saves a new Google Sheet URL to the backend.
   */
  async saveSheetUrl(url: string): Promise<boolean> {
    try {
      const response = await fetch(`${API_BASE}/api/config/sheet-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      return response.ok;
    } catch (error) {
      console.error('Error saving sheet URL:', error);
      return false;
    }
  },

  /**
   * Triggers a batch of outbound calls (Legacy/Manual).
   */
  async triggerBatchCalls(numbers: string[]): Promise<{ success: boolean; batch_id: string }> {
    try {
      const response = await fetch(`${API_BASE}/api/outbound/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_numbers: numbers }),
      });

      if (!response.ok) {
        throw new Error('Failed to trigger batch calls');
      }

      return await response.json();
    } catch (error) {
      console.error('Error in triggerBatchCalls:', error);
      throw error;
    }
  },

  /**
   * Fetches the current call queue status.
   */
  async getQueueStatus(): Promise<QueueItem[]> {
    try {
      const response = await fetch(`${API_BASE}/api/outbound/queue`);
      if (!response.ok) {
        // Fallback for development if endpoint doesn't exist yet
        return this.getMockQueue();
      }
      return await response.json();
    } catch (error) {
      console.warn('Queue API error, using mock data:', error);
      return this.getMockQueue();
    }
  },

  /**
   * Mock data for development and demonstration.
   */
  getMockQueue(): QueueItem[] {
    return [
      { id: '1', phone: '+919988776655', status: 'success', timestamp: new Date(Date.now() - 3600000).toISOString(), duration: '2m 15s' },
      { id: '2', phone: '+917766554433', status: 'failed', timestamp: new Date(Date.now() - 1800000).toISOString(), error: 'No Answer' },
      { id: '3', phone: '+918877665544', status: 'calling', timestamp: new Date(Date.now() - 60000).toISOString() },
      { id: '4', phone: '+916655443322', status: 'pending', timestamp: new Date().toISOString() },
    ];
  }
};
