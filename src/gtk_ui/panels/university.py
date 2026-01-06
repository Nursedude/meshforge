"""
MeshForge University Panel - In-App Learning System

Provides courses, lessons, and assessments for mesh networking education.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class UniversityPanel(Gtk.Box):
    """MeshForge University learning panel"""

    def __init__(self, parent_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.parent_window = parent_window

        # Initialize university components
        try:
            from university import CourseManager, ProgressTracker
            from university.assessments import AssessmentManager
            self.course_manager = CourseManager()
            self.progress_tracker = ProgressTracker()
            self.assessment_manager = AssessmentManager()
        except ImportError as e:
            logger.error(f"Failed to import university modules: {e}")
            self.course_manager = None
            self.progress_tracker = None
            self.assessment_manager = None

        # State
        self.current_course = None
        self.current_lesson = None
        self.current_lesson_index = 0
        self.current_assessment = None
        self.assessment_answers = {}

        self._build_ui()

    def _build_ui(self):
        """Build the university UI"""
        # Main content with navigation stack
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.append(self.main_stack)

        # Page 1: Course catalog
        self._build_catalog_page()

        # Page 2: Course detail (lessons list)
        self._build_course_page()

        # Page 3: Lesson viewer
        self._build_lesson_page()

        # Page 4: Assessment
        self._build_assessment_page()

        # Start on catalog
        self.main_stack.set_visible_child_name("catalog")

    def _build_catalog_page(self):
        """Build the course catalog page"""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(10)
        page.set_margin_bottom(10)

        # Header with stats
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header_box.set_margin_bottom(10)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        title = Gtk.Label(label="MeshForge University")
        title.add_css_class("title-1")
        title.set_xalign(0)
        title_box.append(title)

        subtitle = Gtk.Label(label="Learn mesh networking from basics to advanced topics")
        subtitle.add_css_class("dim-label")
        subtitle.set_xalign(0)
        title_box.append(subtitle)
        header_box.append(title_box)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header_box.append(spacer)

        # Stats card
        self.stats_box = self._build_stats_card()
        header_box.append(self.stats_box)

        page.append(header_box)

        # Difficulty filter
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        filter_box.set_margin_bottom(10)

        filter_label = Gtk.Label(label="Filter by level:")
        filter_label.add_css_class("dim-label")
        filter_box.append(filter_label)

        self.filter_dropdown = Gtk.DropDown.new_from_strings([
            "All Levels", "Beginner", "Intermediate", "Advanced", "Expert"
        ])
        self.filter_dropdown.set_selected(0)
        self.filter_dropdown.connect("notify::selected", self._on_filter_changed)
        filter_box.append(self.filter_dropdown)

        page.append(filter_box)

        # Course list in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.course_list = Gtk.ListBox()
        self.course_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.course_list.add_css_class("boxed-list")
        scrolled.set_child(self.course_list)

        page.append(scrolled)

        # Populate courses
        self._populate_courses()

        self.main_stack.add_named(page, "catalog")

    def _build_stats_card(self) -> Gtk.Box:
        """Build the statistics card"""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        card.add_css_class("card")
        card.set_margin_start(10)
        card.set_margin_end(10)
        card.set_margin_top(5)
        card.set_margin_bottom(5)

        if not self.progress_tracker:
            return card

        stats = self.progress_tracker.get_overall_stats()

        # Progress indicator
        progress_label = Gtk.Label(label="Your Progress")
        progress_label.add_css_class("heading")
        card.append(progress_label)

        # Lessons completed
        lessons_text = f"{stats['completed_lessons']}/{stats['total_lessons']} lessons"
        lessons_label = Gtk.Label(label=lessons_text)
        lessons_label.add_css_class("dim-label")
        card.append(lessons_label)

        # Courses completed
        courses_text = f"{stats['completed_courses']} courses completed"
        courses_label = Gtk.Label(label=courses_text)
        courses_label.add_css_class("dim-label")
        card.append(courses_label)

        # Average score if available
        if stats['average_score'] is not None:
            score_text = f"Avg score: {stats['average_score']:.0f}%"
            score_label = Gtk.Label(label=score_text)
            score_label.add_css_class("dim-label")
            card.append(score_label)

        return card

    def _populate_courses(self, difficulty_filter: Optional[str] = None):
        """Populate the course list"""
        # Clear existing
        while True:
            child = self.course_list.get_first_child()
            if child is None:
                break
            self.course_list.remove(child)

        if not self.course_manager:
            error_row = Gtk.ListBoxRow()
            error_label = Gtk.Label(label="Course system unavailable")
            error_label.add_css_class("error")
            error_row.set_child(error_label)
            self.course_list.append(error_row)
            return

        # Get courses
        courses = self.course_manager.get_all_courses()

        # Filter by difficulty if specified
        if difficulty_filter and difficulty_filter != "All Levels":
            from university.courses import Difficulty
            diff_map = {
                "Beginner": Difficulty.BEGINNER,
                "Intermediate": Difficulty.INTERMEDIATE,
                "Advanced": Difficulty.ADVANCED,
                "Expert": Difficulty.EXPERT,
            }
            if difficulty_filter in diff_map:
                courses = [c for c in courses if c.difficulty == diff_map[difficulty_filter]]

        # Create rows
        for course in courses:
            row = self._create_course_row(course)
            self.course_list.append(row)

    def _create_course_row(self, course) -> Gtk.ListBoxRow:
        """Create a row for a course"""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        # Course icon
        icon = Gtk.Image.new_from_icon_name(course.icon)
        icon.set_pixel_size(48)
        icon.add_css_class("dim-label")
        box.append(icon)

        # Course info
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info_box.set_hexpand(True)

        title = Gtk.Label(label=course.title)
        title.add_css_class("heading")
        title.set_xalign(0)
        info_box.append(title)

        desc = Gtk.Label(label=course.description)
        desc.add_css_class("dim-label")
        desc.set_xalign(0)
        desc.set_wrap(True)
        desc.set_wrap_mode(Pango.WrapMode.WORD)
        info_box.append(desc)

        # Metadata row
        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        difficulty_label = Gtk.Label(label=course.difficulty.value.title())
        difficulty_label.add_css_class("caption")
        if course.difficulty.value == "beginner":
            difficulty_label.add_css_class("success")
        elif course.difficulty.value == "advanced":
            difficulty_label.add_css_class("warning")
        elif course.difficulty.value == "expert":
            difficulty_label.add_css_class("error")
        meta_box.append(difficulty_label)

        lessons_label = Gtk.Label(label=f"{len(course.lessons)} lessons")
        lessons_label.add_css_class("caption")
        lessons_label.add_css_class("dim-label")
        meta_box.append(lessons_label)

        time_label = Gtk.Label(label=f"~{course.estimated_hours:.1f}h")
        time_label.add_css_class("caption")
        time_label.add_css_class("dim-label")
        meta_box.append(time_label)

        info_box.append(meta_box)

        # Progress bar
        if self.progress_tracker:
            progress = self.progress_tracker.get_course_progress(course.id)
            # Initialize lesson count if needed
            for lesson in course.lessons:
                if lesson.id not in progress.lessons:
                    progress.lessons[lesson.id] = None  # Placeholder

            if progress.total_count > 0:
                progress_bar = Gtk.ProgressBar()
                progress_bar.set_fraction(progress.completed_count / len(course.lessons))
                progress_bar.set_margin_top(5)
                info_box.append(progress_bar)

        box.append(info_box)

        # Start button
        start_btn = Gtk.Button(label="Start" if not self._get_course_progress_pct(course.id) else "Continue")
        start_btn.add_css_class("suggested-action")
        start_btn.set_valign(Gtk.Align.CENTER)
        start_btn.set_tooltip_text(f"Begin {course.title}")
        start_btn.connect("clicked", self._on_course_start, course)
        box.append(start_btn)

        row.set_child(box)
        return row

    def _get_course_progress_pct(self, course_id: str) -> float:
        """Get course completion percentage"""
        if not self.progress_tracker:
            return 0.0
        progress = self.progress_tracker.get_course_progress(course_id)
        if not progress.lessons:
            return 0.0
        return progress.percentage

    def _on_filter_changed(self, dropdown, param):
        """Handle filter change"""
        selected = dropdown.get_selected()
        filters = ["All Levels", "Beginner", "Intermediate", "Advanced", "Expert"]
        self._populate_courses(filters[selected] if selected < len(filters) else None)

    def _on_course_start(self, button, course):
        """Handle course start"""
        try:
            logger.debug(f"[University] Starting course: {course.title}")
            self.current_course = course
            self.current_lesson_index = 0

            # Find first incomplete lesson
            if self.progress_tracker:
                progress = self.progress_tracker.get_course_progress(course.id)
                for i, lesson in enumerate(course.lessons):
                    lesson_prog = progress.lessons.get(lesson.id)
                    if lesson_prog is None or not lesson_prog.completed:
                        self.current_lesson_index = i
                        break

            self._show_course_page()
            logger.debug(f"[University] Course page shown for: {course.title}")
        except Exception as e:
            logger.error(f"[University] Error starting course: {e}", exc_info=True)

    def _build_course_page(self):
        """Build the course detail page"""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(10)
        page.set_margin_bottom(10)

        # Header with back button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        back_btn = Gtk.Button()
        back_btn.set_icon_name("go-previous-symbolic")
        back_btn.set_tooltip_text("Back to catalog")
        back_btn.connect("clicked", lambda b: self.main_stack.set_visible_child_name("catalog"))
        header.append(back_btn)

        self.course_title_label = Gtk.Label(label="Course Title")
        self.course_title_label.add_css_class("title-2")
        self.course_title_label.set_xalign(0)
        self.course_title_label.set_hexpand(True)
        header.append(self.course_title_label)

        page.append(header)

        # Lesson list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.lesson_list = Gtk.ListBox()
        self.lesson_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.lesson_list.add_css_class("boxed-list")
        scrolled.set_child(self.lesson_list)

        page.append(scrolled)

        self.main_stack.add_named(page, "course")

    def _show_course_page(self):
        """Show the course page with lessons"""
        try:
            if not self.current_course:
                logger.warning("[University] Cannot show course page - no course selected")
                return

            self.course_title_label.set_label(self.current_course.title)

            # Clear lesson list
            while True:
                child = self.lesson_list.get_first_child()
                if child is None:
                    break
                self.lesson_list.remove(child)

            # Get progress
            progress = None
            if self.progress_tracker:
                progress = self.progress_tracker.get_course_progress(self.current_course.id)

            # Add lessons
            for i, lesson in enumerate(self.current_course.lessons):
                row = self._create_lesson_row(lesson, i, progress)
                self.lesson_list.append(row)

            self.main_stack.set_visible_child_name("course")
        except Exception as e:
            logger.error(f"[University] Error showing course page: {e}", exc_info=True)

    def _create_lesson_row(self, lesson, index: int, progress) -> Gtk.ListBoxRow:
        """Create a row for a lesson"""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Lesson number
        num_label = Gtk.Label(label=f"{index + 1}.")
        num_label.set_width_chars(3)
        num_label.add_css_class("dim-label")
        box.append(num_label)

        # Status icon
        is_complete = False
        if progress and lesson.id in progress.lessons:
            lesson_prog = progress.lessons[lesson.id]
            if lesson_prog and lesson_prog.completed:
                is_complete = True

        status_icon = Gtk.Image.new_from_icon_name(
            "emblem-ok-symbolic" if is_complete else "radio-symbolic"
        )
        status_icon.add_css_class("success" if is_complete else "dim-label")
        box.append(status_icon)

        # Lesson info
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        title = Gtk.Label(label=lesson.title)
        title.set_xalign(0)
        if is_complete:
            title.add_css_class("dim-label")
        info_box.append(title)

        meta = Gtk.Label(label=f"{lesson.duration_minutes} min" + (" • Has quiz" if lesson.has_assessment else ""))
        meta.add_css_class("caption")
        meta.add_css_class("dim-label")
        meta.set_xalign(0)
        info_box.append(meta)

        box.append(info_box)

        # Start button
        btn_label = "Review" if is_complete else "Start"
        start_btn = Gtk.Button(label=btn_label)
        if not is_complete:
            start_btn.add_css_class("suggested-action")
        start_btn.set_tooltip_text(f"Open lesson: {lesson.title}")
        start_btn.connect("clicked", self._on_lesson_start, lesson, index)
        box.append(start_btn)

        row.set_child(box)
        return row

    def _on_lesson_start(self, button, lesson, index):
        """Handle lesson start"""
        try:
            logger.debug(f"[University] Starting lesson: {lesson.title}")
            self.current_lesson = lesson
            self.current_lesson_index = index

            # Track start
            if self.progress_tracker and self.current_course:
                self.progress_tracker.start_lesson(self.current_course.id, lesson.id)

            self._show_lesson_page()
            logger.debug(f"[University] Lesson page shown for: {lesson.title}")
        except Exception as e:
            logger.error(f"[University] Error starting lesson: {e}", exc_info=True)

    def _build_lesson_page(self):
        """Build the lesson viewer page"""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header with navigation
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(10)
        header.set_margin_bottom(10)

        back_btn = Gtk.Button()
        back_btn.set_icon_name("go-previous-symbolic")
        back_btn.set_tooltip_text("Back to course")
        back_btn.connect("clicked", lambda b: self._show_course_page())
        header.append(back_btn)

        self.lesson_title_label = Gtk.Label(label="Lesson Title")
        self.lesson_title_label.add_css_class("title-3")
        self.lesson_title_label.set_xalign(0)
        self.lesson_title_label.set_hexpand(True)
        header.append(self.lesson_title_label)

        # Panel link button (if applicable)
        self.panel_link_btn = Gtk.Button()
        self.panel_link_btn.set_icon_name("go-next-symbolic")
        self.panel_link_btn.set_tooltip_text("Go to related panel")
        self.panel_link_btn.connect("clicked", self._on_panel_link)
        self.panel_link_btn.set_visible(False)
        header.append(self.panel_link_btn)

        page.append(header)

        # Content area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_start(20)
        content_box.set_margin_end(20)

        self.lesson_content = Gtk.Label()
        self.lesson_content.set_wrap(True)
        self.lesson_content.set_wrap_mode(Pango.WrapMode.WORD)
        self.lesson_content.set_xalign(0)
        self.lesson_content.set_yalign(0)
        self.lesson_content.set_selectable(True)
        self.lesson_content.set_use_markup(True)
        content_box.append(self.lesson_content)

        scrolled.set_child(content_box)
        page.append(scrolled)

        # Bottom navigation
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        nav_box.set_margin_start(20)
        nav_box.set_margin_end(20)
        nav_box.set_margin_top(10)
        nav_box.set_margin_bottom(10)
        nav_box.add_css_class("toolbar")

        self.prev_lesson_btn = Gtk.Button(label="Previous")
        self.prev_lesson_btn.set_icon_name("go-previous-symbolic")
        self.prev_lesson_btn.connect("clicked", self._on_prev_lesson)
        nav_box.append(self.prev_lesson_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        nav_box.append(spacer)

        self.lesson_progress_label = Gtk.Label(label="1 of 5")
        self.lesson_progress_label.add_css_class("dim-label")
        nav_box.append(self.lesson_progress_label)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        nav_box.append(spacer2)

        self.next_lesson_btn = Gtk.Button(label="Next")
        self.next_lesson_btn.add_css_class("suggested-action")
        self.next_lesson_btn.connect("clicked", self._on_next_lesson)
        nav_box.append(self.next_lesson_btn)

        page.append(nav_box)

        self.main_stack.add_named(page, "lesson")

    def _show_lesson_page(self):
        """Show the lesson content"""
        try:
            if not self.current_lesson or not self.current_course:
                logger.warning("[University] Cannot show lesson - no lesson/course selected")
                return

            self.lesson_title_label.set_label(self.current_lesson.title)

            # Convert markdown to simple markup (basic conversion)
            content = self._markdown_to_markup(self.current_lesson.content)
            self.lesson_content.set_markup(content)

            # Update navigation
            total = len(self.current_course.lessons)
            self.lesson_progress_label.set_label(f"{self.current_lesson_index + 1} of {total}")

            self.prev_lesson_btn.set_sensitive(self.current_lesson_index > 0)

            # Check if this lesson has assessment
            if self.current_lesson.has_assessment and self.assessment_manager:
                if self.assessment_manager.has_assessment(self.current_lesson.id):
                    self.next_lesson_btn.set_label("Take Quiz")
                else:
                    self.next_lesson_btn.set_label("Complete" if self.current_lesson_index >= total - 1 else "Next")
            else:
                self.next_lesson_btn.set_label("Complete" if self.current_lesson_index >= total - 1 else "Next")

            # Panel link
            if self.current_lesson.panel_reference:
                self.panel_link_btn.set_visible(True)
                self.panel_link_btn.set_tooltip_text(f"Go to {self.current_lesson.panel_reference} panel")
            else:
                self.panel_link_btn.set_visible(False)

            self.main_stack.set_visible_child_name("lesson")
        except Exception as e:
            logger.error(f"[University] Error showing lesson page: {e}", exc_info=True)

    def _markdown_to_markup(self, text: str) -> str:
        """Convert basic markdown to Pango markup"""
        import re

        # Escape existing markup
        text = GLib.markup_escape_text(text)

        # Headers
        text = re.sub(r'^### (.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
        text = re.sub(r'^## (.+)$', r'<span size="large"><b>\1</b></span>', text, flags=re.MULTILINE)
        text = re.sub(r'^# (.+)$', r'<span size="x-large"><b>\1</b></span>', text, flags=re.MULTILINE)

        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

        # Code blocks (simplified - just make monospace)
        text = re.sub(r'```[a-z]*\n(.*?)```', r'<tt>\1</tt>', text, flags=re.DOTALL)
        text = re.sub(r'`([^`]+)`', r'<tt>\1</tt>', text)

        return text

    def _on_panel_link(self, button):
        """Navigate to linked panel"""
        if self.current_lesson and self.current_lesson.panel_reference:
            # Navigate to the panel in the main app
            if hasattr(self.parent_window, 'content_stack'):
                self.parent_window.content_stack.set_visible_child_name(self.current_lesson.panel_reference)

    def _on_prev_lesson(self, button):
        """Go to previous lesson"""
        try:
            if self.current_lesson_index > 0 and self.current_course:
                self.current_lesson_index -= 1
                self.current_lesson = self.current_course.lessons[self.current_lesson_index]
                logger.debug(f"[University] Previous lesson: {self.current_lesson.title}")
                self._show_lesson_page()
        except Exception as e:
            logger.error(f"[University] Error going to previous lesson: {e}", exc_info=True)

    def _on_next_lesson(self, button):
        """Go to next lesson or assessment"""
        try:
            if not self.current_course or not self.current_lesson:
                logger.warning("[University] Next clicked but no course/lesson selected")
                return

            logger.debug(f"[University] Next from lesson: {self.current_lesson.title}")

            # Check for assessment
            if self.current_lesson.has_assessment and self.assessment_manager:
                assessment = self.assessment_manager.get_assessment(self.current_lesson.id)
                if assessment:
                    logger.debug(f"[University] Showing assessment for: {self.current_lesson.id}")
                    self._show_assessment_page(assessment)
                    return

            # Complete current lesson
            self._complete_current_lesson()

            # Move to next or back to course
            total = len(self.current_course.lessons)
            if self.current_lesson_index < total - 1:
                self.current_lesson_index += 1
                self.current_lesson = self.current_course.lessons[self.current_lesson_index]
                logger.debug(f"[University] Moving to lesson: {self.current_lesson.title}")
                if self.progress_tracker:
                    self.progress_tracker.start_lesson(self.current_course.id, self.current_lesson.id)
                self._show_lesson_page()
            else:
                # Course complete
                logger.debug(f"[University] Course complete: {self.current_course.title}")
                self._show_course_complete()
        except Exception as e:
            logger.error(f"[University] Error going to next lesson: {e}", exc_info=True)

    def _complete_current_lesson(self, score: Optional[float] = None):
        """Mark current lesson as complete"""
        if self.progress_tracker and self.current_course and self.current_lesson:
            self.progress_tracker.complete_lesson(
                self.current_course.id,
                self.current_lesson.id,
                score
            )

    def _show_course_complete(self):
        """Show course completion message"""
        if self.parent_window:
            self.parent_window.show_info_dialog(
                "Course Complete!",
                f"Congratulations! You've completed {self.current_course.title}.\n\n"
                "Check your progress in the course catalog."
            )
        self._show_course_page()

    def _build_assessment_page(self):
        """Build the assessment/quiz page"""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(10)
        page.set_margin_bottom(10)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self.assessment_title = Gtk.Label(label="Quiz")
        self.assessment_title.add_css_class("title-2")
        self.assessment_title.set_xalign(0)
        self.assessment_title.set_hexpand(True)
        header.append(self.assessment_title)

        page.append(header)

        # Questions area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.questions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        scrolled.set_child(self.questions_box)

        page.append(scrolled)

        # Submit button
        submit_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        submit_box.append(spacer)

        self.submit_btn = Gtk.Button(label="Submit Answers")
        self.submit_btn.add_css_class("suggested-action")
        self.submit_btn.connect("clicked", self._on_submit_assessment)
        submit_box.append(self.submit_btn)

        page.append(submit_box)

        self.main_stack.add_named(page, "assessment")

    def _show_assessment_page(self, assessment):
        """Show the assessment questions"""
        self.current_assessment = assessment
        self.assessment_answers = {}

        self.assessment_title.set_label(assessment.title)

        # Clear questions
        while True:
            child = self.questions_box.get_first_child()
            if child is None:
                break
            self.questions_box.remove(child)

        # Add questions
        questions = assessment.get_questions()
        for i, question in enumerate(questions):
            q_box = self._create_question_widget(question, i + 1)
            self.questions_box.append(q_box)

        self.main_stack.set_visible_child_name("assessment")

    def _create_question_widget(self, question, number: int) -> Gtk.Box:
        """Create a widget for a question"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("card")
        box.set_margin_start(5)
        box.set_margin_end(5)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        # Question text
        q_label = Gtk.Label(label=f"{number}. {question.text}")
        q_label.set_wrap(True)
        q_label.set_wrap_mode(Pango.WrapMode.WORD)
        q_label.set_xalign(0)
        q_label.add_css_class("heading")
        box.append(q_label)

        # Answer options (radio buttons)
        group = None
        for answer in question.answers:
            radio = Gtk.CheckButton(label=answer.text)
            if group:
                radio.set_group(group)
            else:
                group = radio

            radio.connect("toggled", self._on_answer_selected, question.id, answer.id)
            box.append(radio)

        return box

    def _on_answer_selected(self, radio, question_id: str, answer_id: str):
        """Handle answer selection"""
        if radio.get_active():
            self.assessment_answers[question_id] = answer_id

    def _on_submit_assessment(self, button):
        """Submit assessment and show results"""
        if not self.current_assessment:
            logger.warning("[University] Submit clicked but no assessment loaded")
            return

        # Calculate score
        result = self.current_assessment.calculate_score(self.assessment_answers)

        # Complete lesson with score
        self._complete_current_lesson(result['percentage'])

        # Show results
        passed = result['passed']
        message = f"Score: {result['percentage']:.0f}%\n\n"
        if passed:
            message += "Congratulations! You passed the quiz."
        else:
            message += f"You need {self.current_assessment.passing_score:.0f}% to pass. Try again!"

        # Show incorrect answers
        incorrect = [d for d in result['details'] if not d['correct']]
        if incorrect:
            message += f"\n\n{len(incorrect)} question(s) incorrect."

        if self.parent_window:
            self.parent_window.show_info_dialog(
                "Quiz Results",
                message
            )

        # Continue to next lesson if passed
        if passed:
            total = len(self.current_course.lessons)
            if self.current_lesson_index < total - 1:
                self.current_lesson_index += 1
                self.current_lesson = self.current_course.lessons[self.current_lesson_index]
                if self.progress_tracker:
                    self.progress_tracker.start_lesson(self.current_course.id, self.current_lesson.id)
                self._show_lesson_page()
            else:
                self._show_course_complete()
        else:
            # Go back to lesson to review
            self._show_lesson_page()
