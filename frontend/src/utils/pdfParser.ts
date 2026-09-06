import * as pdfjs from 'pdfjs-dist';

// Set worker source for pdfjs-dist
// In a Vite environment, we can use the default worker from the package
pdfjs.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.mjs`;

/**
 * Extracts phone numbers from a PDF file.
 * @param file The PDF file to parse
 * @returns A promise that resolves to an array of unique phone numbers
 */
export const extractPhoneNumbersFromPDF = async (file: File): Promise<string[]> => {
  try {
    const arrayBuffer = await file.arrayBuffer();
    const pdf = await pdfjs.getDocument({ data: arrayBuffer }).promise;
    let fullText = '';

    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const textContent = await page.getTextContent();
      const pageText = textContent.items
        .map((item: any) => item.str)
        .join(' ');
      fullText += pageText + ' ';
    }

    return extractPhoneNumbersFromText(fullText);
  } catch (error) {
    console.error('Error parsing PDF:', error);
    throw new Error('Failed to extract phone numbers from PDF');
  }
};

/**
 * Extracts phone numbers from raw text using regex.
 * Matches standard international and domestic formats.
 */
export const extractPhoneNumbersFromText = (text: string): string[] => {
  // Regex for phone numbers: 
  // Supports +CC, spaces, dashes, parentheses, 10-12 digits
  const phoneRegex = /(?:\+?\d{1,3}[-.\s]?)?\(?\d{3,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}/g;
  
  const matches = text.match(phoneRegex);
  if (!matches) return [];

  // Clean and deduplicate
  const cleanedNumbers = matches.map(num => num.replace(/[^\d+]/g, ''));
  return Array.from(new Set(cleanedNumbers)).filter(num => num.length >= 10);
};
