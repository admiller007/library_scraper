#!/usr/bin/env python3
"""
Library Events GUI Viewer
A simple tkinter-based GUI to view and filter library events.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pandas as pd
import webbrowser
from datetime import datetime
from typing import List, Dict, Any
import csv
import os

class LibraryEventsGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Library Events Viewer")
        self.root.geometry("1200x800")
        
        # Data storage
        self.all_events = []
        self.filtered_events = []
        
        # Setup GUI components
        self.setup_gui()
        
        # Load data automatically if CSV exists
        self.auto_load_data()
    
    def setup_gui(self):
        """Setup the main GUI components"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # Title
        title_label = ttk.Label(main_frame, text="Library Events Viewer", 
                               font=('Arial', 16, 'bold'))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 10))
        
        # Control frame
        control_frame = ttk.LabelFrame(main_frame, text="Filters", padding="5")
        control_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), 
                          pady=(0, 10))
        control_frame.columnconfigure(1, weight=1)
        control_frame.columnconfigure(3, weight=1)
        
        # Library filter
        ttk.Label(control_frame, text="Library:").grid(row=0, column=0, padx=(0, 5))
        self.library_var = tk.StringVar(value="All")
        self.library_combo = ttk.Combobox(control_frame, textvariable=self.library_var,
                                         width=20, state="readonly")
        self.library_combo.grid(row=0, column=1, padx=(0, 10), sticky=(tk.W, tk.E))
        
        # Search filter
        ttk.Label(control_frame, text="Search:").grid(row=0, column=2, padx=(10, 5))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(control_frame, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=3, padx=(0, 10), sticky=(tk.W, tk.E))
        
        # Filter button
        filter_btn = ttk.Button(control_frame, text="Apply Filters", 
                               command=self.apply_filters)
        filter_btn.grid(row=0, column=4, padx=(10, 0))
        
        # Load data button
        load_btn = ttk.Button(control_frame, text="Load CSV", 
                             command=self.load_csv_file)
        load_btn.grid(row=0, column=5, padx=(10, 0))
        
        # Events tree view
        tree_frame = ttk.Frame(main_frame)
        tree_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        
        # Treeview with scrollbars
        self.tree = ttk.Treeview(tree_frame, columns=('Date', 'Time', 'Library', 'Title', 'Location'), 
                                show='tree headings', height=20)
        
        # Configure columns
        self.tree.heading('#0', text='#')
        self.tree.heading('Date', text='Date')
        self.tree.heading('Time', text='Time')
        self.tree.heading('Library', text='Library')
        self.tree.heading('Title', text='Title')
        self.tree.heading('Location', text='Location')
        
        # Column widths
        self.tree.column('#0', width=50, minwidth=50)
        self.tree.column('Date', width=150, minwidth=100)
        self.tree.column('Time', width=100, minwidth=80)
        self.tree.column('Library', width=150, minwidth=100)
        self.tree.column('Title', width=300, minwidth=200)
        self.tree.column('Location', width=200, minwidth=150)
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Grid treeview and scrollbars
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        v_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        h_scrollbar.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # Event details frame
        details_frame = ttk.LabelFrame(main_frame, text="Event Details", padding="5")
        details_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), 
                          pady=(10, 0))
        details_frame.columnconfigure(0, weight=1)
        
        # Details text widget
        self.details_text = tk.Text(details_frame, height=6, wrap=tk.WORD, 
                                   font=('Arial', 10))
        details_scrollbar = ttk.Scrollbar(details_frame, orient=tk.VERTICAL, 
                                         command=self.details_text.yview)
        self.details_text.configure(yscrollcommand=details_scrollbar.set)
        
        self.details_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        details_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # Buttons frame
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.grid(row=4, column=0, columnspan=3, pady=(10, 0))
        
        # Action buttons
        ttk.Button(buttons_frame, text="Open Link", 
                  command=self.open_event_link).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(buttons_frame, text="Export Filtered", 
                  command=self.export_filtered).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons_frame, text="Refresh Data", 
                  command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, 
                              relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=5, column=0, columnspan=3, sticky=(tk.W, tk.E), 
                       pady=(10, 0))
        
        # Bind events
        self.tree.bind('<<TreeviewSelect>>', self.on_item_select)
        self.search_entry.bind('<Return>', lambda e: self.apply_filters())
        self.library_combo.bind('<<ComboboxSelected>>', lambda e: self.apply_filters())
    
    def auto_load_data(self):
        """Automatically load the most recent CSV file if it exists"""
        # Look for CSV files with the pattern
        csv_files = [f for f in os.listdir('.') if f.startswith('all_library_events_') and f.endswith('.csv')]
        
        if csv_files:
            # Get the most recent file
            latest_file = max(csv_files, key=lambda x: os.path.getmtime(x))
            self.load_csv_data(latest_file)
            self.status_var.set(f"Loaded: {latest_file}")
        else:
            self.status_var.set("No CSV files found. Use 'Load CSV' to load data.")
    
    def load_csv_file(self):
        """Load CSV file via file dialog"""
        filename = filedialog.askopenfilename(
            title="Select Library Events CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if filename:
            self.load_csv_data(filename)
            self.status_var.set(f"Loaded: {os.path.basename(filename)}")
    
    def load_csv_data(self, filename):
        """Load event data from CSV file"""
        try:
            df = pd.read_csv(filename)
            self.all_events = df.to_dict('records')
            
            # Update library filter options
            libraries = sorted(df['Library'].unique().tolist())
            self.library_combo['values'] = ['All'] + libraries
            
            # Apply initial filter (show all)
            self.apply_filters()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load CSV file:\n{e}")
    
    def apply_filters(self):
        """Apply current filters to the event list"""
        if not self.all_events:
            return
        
        filtered = self.all_events.copy()
        
        # Library filter
        library_filter = self.library_var.get()
        if library_filter != "All":
            filtered = [e for e in filtered if e.get('Library', '') == library_filter]
        
        # Search filter
        search_term = self.search_var.get().lower().strip()
        if search_term:
            filtered = [e for e in filtered if 
                       search_term in e.get('Title', '').lower() or
                       search_term in e.get('Description', '').lower() or
                       search_term in e.get('Location', '').lower()]
        
        self.filtered_events = filtered
        self.update_tree_view()
        
        # Update status
        total = len(self.all_events)
        shown = len(filtered)
        self.status_var.set(f"Showing {shown} of {total} events")
    
    def update_tree_view(self):
        """Update the tree view with filtered events"""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Add filtered events
        for i, event in enumerate(self.filtered_events, 1):
            values = (
                event.get('Date', ''),
                event.get('Time', ''),
                event.get('Library', ''),
                event.get('Title', ''),
                event.get('Location', '')
            )
            self.tree.insert('', 'end', text=str(i), values=values)
    
    def on_item_select(self, event):
        """Handle tree item selection"""
        selection = self.tree.selection()
        if not selection:
            self.details_text.delete(1.0, tk.END)
            return
        
        # Get selected item index
        item = self.tree.item(selection[0])
        try:
            index = int(item['text']) - 1
            if 0 <= index < len(self.filtered_events):
                event = self.filtered_events[index]
                self.show_event_details(event)
        except (ValueError, IndexError):
            pass
    
    def show_event_details(self, event):
        """Display detailed information for the selected event"""
        self.details_text.delete(1.0, tk.END)
        
        details = []
        details.append(f"Title: {event.get('Title', 'N/A')}")
        details.append(f"Library: {event.get('Library', 'N/A')}")
        details.append(f"Date: {event.get('Date', 'N/A')}")
        details.append(f"Time: {event.get('Time', 'N/A')}")
        details.append(f"Location: {event.get('Location', 'N/A')}")
        details.append(f"Age Group: {event.get('Age Group', 'N/A')}")
        
        if event.get('Description') and event['Description'] != 'Not found':
            details.append(f"\nDescription:\n{event['Description']}")
        
        if event.get('Link') and event['Link'] not in ['N/A', '']:
            details.append(f"\nLink: {event['Link']}")
        
        self.details_text.insert(1.0, '\n'.join(details))
    
    def open_event_link(self):
        """Open the event link in web browser"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select an event first.")
            return
        
        item = self.tree.item(selection[0])
        try:
            index = int(item['text']) - 1
            if 0 <= index < len(self.filtered_events):
                event = self.filtered_events[index]
                link = event.get('Link', '')
                if link and link not in ['N/A', '']:
                    webbrowser.open(link)
                else:
                    messagebox.showinfo("No Link", "This event has no associated link.")
        except (ValueError, IndexError):
            messagebox.showerror("Error", "Unable to open link.")
    
    def export_filtered(self):
        """Export filtered events to CSV"""
        if not self.filtered_events:
            messagebox.showwarning("No Data", "No events to export.")
            return
        
        filename = filedialog.asksaveasfilename(
            title="Save Filtered Events",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                df = pd.DataFrame(self.filtered_events)
                df.to_csv(filename, index=False, quoting=csv.QUOTE_ALL)
                messagebox.showinfo("Success", f"Exported {len(self.filtered_events)} events to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export data:\n{e}")
    
    def refresh_data(self):
        """Refresh data by running the scraper"""
        response = messagebox.askyesno(
            "Refresh Data", 
            "This will run the library scraper to fetch new data. This may take a few minutes. Continue?"
        )
        
        if response:
            self.status_var.set("Fetching new data...")
            self.root.update()
            
            try:
                # Import and run the main scraper
                import subprocess
                result = subprocess.run(['python3', 'library.py'], 
                                      capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    self.auto_load_data()  # Reload the data
                    messagebox.showinfo("Success", "Data refreshed successfully!")
                else:
                    messagebox.showerror("Error", f"Scraper failed:\n{result.stderr}")
                    self.status_var.set("Ready")
            except subprocess.TimeoutExpired:
                messagebox.showerror("Error", "Scraper timed out after 5 minutes.")
                self.status_var.set("Ready")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to run scraper:\n{e}")
                self.status_var.set("Ready")

def main():
    root = tk.Tk()
    app = LibraryEventsGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()